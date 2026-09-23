from __future__ import annotations

import argparse
from pathlib import Path

from pyrosm import OSM


ROAD_TYPES = [
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "track",
    "cycleway",
]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extrait les voies OSM utiles à l'analyse BAAC vers GeoParquet."
    )
    parser.add_argument(
        "pbf_path",
        type=Path,
        help="Chemin vers le fichier .osm.pbf source.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("osm/derived/roads.geoparquet"),
        help="Chemin du GeoParquet de sortie.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if not args.pbf_path.is_file():
        raise FileNotFoundError(f"PBF introuvable : {args.pbf_path}")

    args.output.parent.mkdir(parents=True, exist_ok=True)

    osm = OSM(args.pbf_path)

    roads = osm.get_network(
        network_type="all",
        extra_attributes=[
            "highway",
            "name",
            "ref",
            "maxspeed",
            "lanes",
            "width",
            "surface",
            "smoothness",
            "lit",
            "oneway",
            "access",
            "bicycle",
            "motor_vehicle",
            "cycleway",
            "cycleway:left",
            "cycleway:right",
            "sidewalk",
            "shoulder",
        ],
    )

    roads = roads.loc[roads["highway"].isin(ROAD_TYPES)].copy()

    if "id" not in roads.columns:
        raise ValueError("La sortie Pyrosm ne contient pas la colonne OSM `id`.")

    roads = roads.rename(columns={"id": "osm_way_id"})

    roads.to_parquet(args.output, index=False)

    print(f"Voies extraites : {len(roads):,}")
    print(f"Types OSM : {roads['highway'].nunique()}")
    print(f"Sortie GeoParquet : {args.output}")


if __name__ == "__main__":
    main()