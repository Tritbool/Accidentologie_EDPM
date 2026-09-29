from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recherche les voies OSM à proximité de points d'accident BAAC."
    )
    parser.add_argument("accidents", type=Path, help="GeoParquet BAAC des accidents")
    parser.add_argument("roads", type=Path, help="GeoParquet OSM des voies")
    parser.add_argument("output", type=Path, help="GeoParquet des candidats et non-appariés")
    parser.add_argument("--radius-m", type=float, default=30.0)
    parser.add_argument(
        "--crs-metric",
        default="EPSG:2154",
        help="CRS métrique du territoire étudié ; EPSG:2154 convient à la métropole.",
    )
    return parser.parse_args()


def main() -> None:
    args = arguments()

    if args.radius_m <= 0:
        raise ValueError("--radius-m doit être strictement positif")

    for source in (args.accidents, args.roads):
        if not source.is_file():
            raise FileNotFoundError(source)

    if args.output.exists():
        raise FileExistsError(
            f"Sortie déjà présente : {args.output}. "
            "Supprime-la explicitement avant de relancer le test."
        )

    accidents = gpd.read_parquet(args.accidents)
    roads = gpd.read_parquet(args.roads)

    if accidents.crs is None or roads.crs is None:
        raise ValueError("Le CRS manque dans au moins un des deux GeoParquet")

    if "Num_Acc" not in accidents.columns:
        raise ValueError("Colonne Num_Acc absente du fichier BAAC")
    if "osm_way_id" not in roads.columns:
        raise ValueError("Colonne osm_way_id absente du fichier OSM")

    accidents = accidents.loc[
        accidents.geometry.notna() & ~accidents.geometry.is_empty,
        ["Num_Acc", "geometry"],
    ].copy()

    roads = roads.loc[
        roads.geometry.notna() & ~roads.geometry.is_empty,
        ["osm_way_id", "highway", "geometry"],
    ].copy()

    accidents = accidents.to_crs(args.crs_metric)
    roads = roads.to_crs(args.crs_metric)

    if roads.empty:
        raise ValueError("Le fichier routier ne contient aucune géométrie")

    xmin, ymin, xmax, ymax = roads.total_bounds
    r = args.radius_m
    accidents = accidents.cx[xmin - r : xmax + r, ymin - r : ymax + r].copy()

    if accidents.empty:
        raise ValueError("Aucun accident dans l'emprise du fichier routier")

    accidents["Num_Acc"] = accidents["Num_Acc"].astype("string")
    roads["osm_way_id"] = roads["osm_way_id"].astype("string")

    candidates = gpd.sjoin(
        accidents,
        roads,
        how="left",
        predicate="dwithin",
        distance=r,
    )

    candidates["distance_m"] = pd.NA
    matched = candidates["index_right"].notna()

    if matched.any():
        right_indices = candidates.loc[matched, "index_right"].astype(int)
        matched_roads = roads.loc[right_indices].geometry.array
        matched_points = candidates.loc[matched].geometry.array

        candidates.loc[matched, "distance_m"] = [
            point.distance(road)
            for point, road in zip(matched_points, matched_roads)
        ]

    candidates["distance_m"] = pd.to_numeric(
        candidates["distance_m"], errors="coerce"
    )
    candidates["match_status"] = "unmatched"
    candidates.loc[matched, "match_status"] = "matched"

    # Garde une seule ligne par accident dans cet extrait OSM :
    # distance minimale ; osm_way_id départage les ex æquo.
    candidates = (
        candidates
        .sort_values(
            ["Num_Acc", "distance_m", "osm_way_id"],
            ascending=[True, True, True],
            na_position="last",
            kind="stable",
        )
        .drop_duplicates(subset="Num_Acc", keep="first")
        .drop(columns="index_right")
        .copy()
    )

    candidates["osm_source"] = args.roads.name
    candidates = candidates.to_crs("EPSG:4326")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    candidates.to_parquet(args.output, index=False)

    print(f"Accidents dans l'emprise : {len(accidents):,}")
    print(f"Accidents avec voie retenue : {(candidates['match_status'] == 'matched').sum():,}")
    print(f"Accidents sans candidat : {(candidates['match_status'] == 'unmatched').sum():,}")
    print(f"Lignes en sortie : {len(candidates):,}")
    print(f"Sortie : {args.output}")

if __name__ == "__main__":
    main()