from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd


REQUIRED_COLUMNS = {
    "Num_Acc",
    "geometry",
    "osm_way_id",
    "highway",
    "distance_m",
    "match_status",
    "osm_source",
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyse un GeoParquet de candidats accident-voie et produit "
            "un rapport Markdown, les attributions les plus proches et les non-appariés."
        )
    )
    parser.add_argument(
        "input_path",
        type=Path,
        help="GeoParquet produit par le matching accident-voie.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/matches/reports"),
        help="Répertoire des sorties.",
    )
    return parser.parse_args()


def markdown_table(dataframe: pd.DataFrame) -> str:
    if dataframe.empty:
        return "_Aucune ligne._"

    return dataframe.to_markdown(index=False)


def main() -> None:
    args = parse_arguments()

    if not args.input_path.is_file():
        raise FileNotFoundError(f"Fichier introuvable : {args.input_path}")

    matches = gpd.read_parquet(args.input_path)

    missing_columns = REQUIRED_COLUMNS - set(matches.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(
            f"Colonnes requises absentes de {args.input_path}: {missing}"
        )

    matches = matches.copy()
    matches["Num_Acc"] = matches["Num_Acc"].astype("string")

    candidates = matches.loc[
        matches["osm_way_id"].notna()
        & matches["match_status"].eq("candidate")
    ].copy()

    unmatched = (
        matches.loc[
            matches["osm_way_id"].isna()
            | matches["match_status"].eq("unmatched")
        ]
        .drop_duplicates(subset=["Num_Acc"])
        .copy()
    )

    nearest = (
        candidates
        .sort_values(
            ["Num_Acc", "distance_m", "osm_way_id"],
            kind="stable",
        )
        .drop_duplicates(subset=["Num_Acc"], keep="first")
        .copy()
    )

    total_accidents = matches["Num_Acc"].nunique()
    matched_accidents = nearest["Num_Acc"].nunique()
    unmatched_accidents = unmatched["Num_Acc"].nunique()

    if matched_accidents + unmatched_accidents != total_accidents:
        raise ValueError(
            "Les partitions matched/unmatched ne recouvrent pas exactement "
            "l'ensemble des accidents du fichier de candidats."
        )

    candidate_count_per_accident = (
        candidates.groupby("Num_Acc")
        .size()
        .rename("candidate_count")
    )

    highway_distribution = (
        nearest["highway"]
        .fillna("<missing>")
        .value_counts(dropna=False)
        .rename_axis("highway")
        .rename("accidents")
        .reset_index()
    )

    candidate_count_distribution = (
        candidate_count_per_accident
        .value_counts()
        .sort_index()
        .rename_axis("candidate_count")
        .rename("accidents")
        .reset_index()
    )

    distances = nearest["distance_m"].describe(
        percentiles=[0.50, 0.75, 0.90, 0.95, 0.99]
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    stem = args.input_path.stem
    nearest_path = args.output_dir / f"{stem}-nearest.geoparquet"
    unmatched_path = args.output_dir / f"{stem}-unmatched.parquet"
    report_path = args.output_dir / f"{stem}.md"

    if nearest_path.exists() or unmatched_path.exists() or report_path.exists():
        existing = [
            str(path)
            for path in (nearest_path, unmatched_path, report_path)
            if path.exists()
        ]
        raise FileExistsError(
            "Sortie déjà existante :\n- " + "\n- ".join(existing)
        )

    nearest.to_parquet(nearest_path, index=False)

    unmatched[
        [
            "Num_Acc",
            "match_status",
            "osm_source",
            "geometry",
        ]
    ].to_parquet(unmatched_path, index=False)

    generated_at = datetime.now(UTC).isoformat()

    report = f"""# Rapport d'appariement accident–route

## Source

- Fichier de candidats : `{args.input_path}`
- Généré UTC : `{generated_at}`
- CRS : `{matches.crs}`

## Résumé

| Indicateur | Valeur |
|---|---:|
| Lignes du fichier de candidats | {len(matches):,} |
| Accidents distincts | {total_accidents:,} |
| Accidents avec au moins un candidat | {matched_accidents:,} |
| Accidents sans candidat | {unmatched_accidents:,} |
| Taux avec au moins un candidat | {matched_accidents / total_accidents:.2%} |
| Lignes candidates | {len(candidates):,} |
| Candidats moyens par accident apparié | {len(candidates) / matched_accidents:.2f} |

## Nombre de candidats

{markdown_table(candidate_count_distribution)}

## Classe OSM retenue

La classe est celle de la voie candidate ayant la plus petite distance pour chaque accident.

{markdown_table(highway_distribution)}

## Distance du candidat retenu

| Statistique | Distance en mètres |
|---|---:|
| Minimum | {distances["min"]:.3f} |
| Médiane | {distances["50%"]:.3f} |
| Percentile 75 | {distances["75%"]:.3f} |
| Percentile 90 | {distances["90%"]:.3f} |
| Percentile 95 | {distances["95%"]:.3f} |
| Percentile 99 | {distances["99%"]:.3f} |
| Maximum | {distances["max"]:.3f} |

## Fichiers produits

- Attribution minimale par accident : `{nearest_path}`
- Accidents sans candidat dans ce fichier : `{unmatched_path}`

## Interprétation

- Un accident est considéré **apparié** dans ce rapport lorsqu'il possède au moins une voie candidate dans le fichier analysé.
- La voie retenue est la candidate de distance minimale.
- Un accident sans candidat dans ce fichier n'est pas nécessairement non apparié globalement : il peut correspondre à une route présente dans un autre extrait OSM régional.
- Ce rapport mesure une couverture de candidats, pas l'exactitude sémantique du rattachement à la bonne voie.
"""

    report_path.write_text(report, encoding="utf-8")

    print(f"Rapport : {report_path}")
    print(f"Voie la plus proche par accident : {nearest_path}")
    print(f"Non-appariés locaux : {unmatched_path}")
    print(f"Accidents : {total_accidents:,}")
    print(f"Taux avec candidat : {matched_accidents / total_accidents:.2%}")


if __name__ == "__main__":
    main()