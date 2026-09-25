from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare les accidents BAAC aux appariements de toutes les régions."
    )
    parser.add_argument("year", type=int, help="Année BAAC, par exemple 2024")
    parser.add_argument(
        "--baac-root",
        type=Path,
        default=Path("BAAC/derived"),
    )
    parser.add_argument(
        "--matches-root",
        type=Path,
        default=Path("outputs/maps"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    baac_path = (
        args.baac_root
        / str(args.year)
        / f"caract-{args.year}.geoparquet"
    )
    matches_dir = args.matches_root / str(args.year)

    if not baac_path.is_file():
        raise FileNotFoundError(f"GeoParquet BAAC absent : {baac_path}")

    if not matches_dir.is_dir():
        raise FileNotFoundError(f"Répertoire de candidats absent : {matches_dir}")

    candidate_files = sorted(
        matches_dir.glob(f"candidates-*-{args.year}-*.geoparquet")
    )

    if not candidate_files:
        raise FileNotFoundError(
            f"Aucun fichier de candidats pour {args.year} dans {matches_dir}"
        )

    con = duckdb.connect()

    files = [str(path) for path in candidate_files]

    baac = con.execute(
        """
        SELECT
            CAST(Num_Acc AS VARCHAR) AS accident_id,
            dep,
            com
        FROM read_parquet(?)
        """,
        [str(baac_path)],
    ).df()

    candidate_rows = con.execute(
        """
        SELECT
            CAST(Num_Acc AS VARCHAR) AS accident_id,
            CAST(osm_way_id AS VARCHAR) AS osm_way_id,
            osm_source
        FROM read_parquet(?, union_by_name = true)
        """,
        [files],
    ).df()

    baac["accident_id"] = (
        baac["accident_id"]
        .astype("string")
        .str.replace(r"\.0$", "", regex=True)
    )
    candidate_rows["accident_id"] = (
        candidate_rows["accident_id"]
        .astype("string")
        .str.replace(r"\.0$", "", regex=True)
    )

    if baac["accident_id"].duplicated().any():
        raise ValueError("Num_Acc non unique dans le GeoParquet BAAC")

    if candidate_rows["accident_id"].isna().any():
        raise ValueError("Num_Acc absent dans au moins un fichier de candidats")

    baac_ids = set(baac["accident_id"])

    matched_ids = set(
        candidate_rows.loc[
            candidate_rows["osm_way_id"].notna(),
            "accident_id",
        ]
    )

    evaluated_ids = set(candidate_rows["accident_id"])

    extra_ids = evaluated_ids - baac_ids
    if extra_ids:
        raise ValueError(
            f"{len(extra_ids)} identifiants de candidats "
            "sont absents du BAAC de cette année"
        )

    matched_ids &= baac_ids
    evaluated_ids &= baac_ids

    evaluated_unmatched_ids = evaluated_ids - matched_ids
    not_evaluated_ids = baac_ids - evaluated_ids
    globally_unmatched_ids = baac_ids - matched_ids

    baac["status"] = "matched"
    baac.loc[
        baac["accident_id"].isin(evaluated_unmatched_ids),
        "status",
    ] = "evaluated_unmatched"
    baac.loc[
        baac["accident_id"].isin(not_evaluated_ids),
        "status",
    ] = "not_evaluated"

    print(f"Année BAAC : {args.year}")
    print(f"Fichiers de candidats : {len(candidate_files):,}")
    print(f"Accidents BAAC géolocalisés : {len(baac_ids):,}")
    print(f"Accidents présents dans ≥ 1 fichier : {len(evaluated_ids):,}")
    print(f"Accidents matchés dans ≥ 1 région : {len(matched_ids):,}")
    print(
        "Évalués, mais sans candidat dans aucune région : "
        f"{len(evaluated_unmatched_ids):,}"
    )
    print(
        "Absents de tous les fichiers de candidats : "
        f"{len(not_evaluated_ids):,}"
    )
    print(
        "Sans appariement global, toutes causes confondues : "
        f"{len(globally_unmatched_ids):,}"
    )
    print(
        "Couverture globale sur les accidents BAAC géolocalisés : "
        f"{len(matched_ids) / len(baac_ids):.2%}"
    )

    print("\nStatut par département BAAC :")
    summary = (
        baac.groupby(["dep", "status"], dropna=False)
        .size()
        .unstack(fill_value=0)
        .sort_index()
    )
    print(summary.to_string())

    output_dir = args.matches_root / "audit"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"global-match-status-{args.year}.parquet"
    if output_path.exists():
        raise FileExistsError(
            f"Audit déjà présent : {output_path}. "
            "Supprime-le explicitement pour recalculer."
        )

    baac.to_parquet(output_path, index=False)
    print(f"\nAudit détaillé : {output_path}")


if __name__ == "__main__":
    main()