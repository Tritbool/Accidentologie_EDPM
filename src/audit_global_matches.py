from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audite la couverture globale des appariements "
            "BAAC–OSM d'une année et produit un CSV par accident."
        )
    )
    parser.add_argument(
        "year",
        type=int,
        help="Année BAAC à auditer, par exemple 2024.",
    )
    parser.add_argument(
        "--baac-root",
        type=Path,
        default=Path("BAAC/derived"),
        help="Racine des données BAAC dérivées.",
    )
    parser.add_argument(
        "--matches-root",
        type=Path,
        default=Path("outputs/maps"),
        help="Racine des fichiers régionaux de candidats.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/audit"),
        help="Répertoire de sortie des CSV d'audit.",
    )
    return parser.parse_args()


def normalize_num_acc(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    invalid = values.notna() & values.ne(values.round())

    if invalid.any():
        examples = series.loc[invalid].head().tolist()
        raise ValueError(
            f"Num_Acc contient des valeurs non entières : {examples}"
        )

    return values.astype("Int64").astype("string")


def main() -> None:
    args = parse_args()

    baac_path = (
        args.baac_root
        / str(args.year)
        / f"caract-{args.year}.geoparquet"
    )
    matches_dir = args.matches_root / str(args.year)

    if not baac_path.is_file():
        raise FileNotFoundError(
            f"GeoParquet BAAC absent : {baac_path}"
        )

    if not matches_dir.is_dir():
        raise FileNotFoundError(
            f"Répertoire de candidats absent : {matches_dir}"
        )

    candidate_files = sorted(
        matches_dir.glob(
            f"candidates-*-{args.year}-*.geoparquet"
        )
    )
    if not candidate_files:
        raise FileNotFoundError(
            f"Aucun fichier de candidats pour {args.year} "
            f"dans {matches_dir}"
        )

    con = duckdb.connect()
    files = [str(path) for path in candidate_files]

    baac = con.execute(
        """
        SELECT
            Num_Acc,
            dep,
            com
        FROM read_parquet(?)
        """,
        [str(baac_path)],
    ).df()

    candidate_rows = con.execute(
        """
        SELECT
            Num_Acc,
            osm_way_id,
            match_status,
            osm_source
        FROM read_parquet(?, union_by_name = true)
        """,
        [files],
    ).df()

    baac["Num_Acc"] = normalize_num_acc(baac["Num_Acc"])
    candidate_rows["Num_Acc"] = normalize_num_acc(
        candidate_rows["Num_Acc"]
    )

    if baac["Num_Acc"].isna().any():
        raise ValueError(
            "Num_Acc absent dans le GeoParquet BAAC"
        )

    if candidate_rows["Num_Acc"].isna().any():
        raise ValueError(
            "Num_Acc absent dans au moins un fichier de candidats"
        )

    if baac["Num_Acc"].duplicated().any():
        duplicates = baac.loc[
            baac["Num_Acc"].duplicated(keep=False),
            "Num_Acc",
        ].head().tolist()
        raise ValueError(
            "Num_Acc non unique dans le GeoParquet BAAC : "
            f"{duplicates}"
        )

    allowed_statuses = {"matched", "unmatched"}
    statuses = set(
        candidate_rows["match_status"].dropna().unique()
    )
    unexpected_statuses = statuses - allowed_statuses
    if unexpected_statuses:
        raise ValueError(
            "Statuts d'appariement inattendus : "
            f"{sorted(unexpected_statuses)}"
        )

    baac_ids = set(baac["Num_Acc"])
    candidate_ids = set(candidate_rows["Num_Acc"])

    extra_ids = candidate_ids - baac_ids
    if extra_ids:
        examples = sorted(extra_ids)[:5]
        raise ValueError(
            f"{len(extra_ids)} identifiants de candidats "
            "sont absents du BAAC de cette année ; "
            f"exemples : {examples}"
        )

    matched_ids = set(
        candidate_rows.loc[
            candidate_rows["match_status"].eq("matched")
            & candidate_rows["osm_way_id"].notna(),
            "Num_Acc",
        ]
    )
    evaluated_ids = candidate_ids
    evaluated_unmatched_ids = evaluated_ids - matched_ids
    not_evaluated_ids = baac_ids - evaluated_ids

    audit = baac.copy()
    audit["annee"] = args.year
    audit["statut_appariement"] = "matched"

    audit.loc[
        audit["Num_Acc"].isin(evaluated_unmatched_ids),
        "statut_appariement",
    ] = "evaluated_unmatched"

    audit.loc[
        audit["Num_Acc"].isin(not_evaluated_ids),
        "statut_appariement",
    ] = "not_evaluated"

    audit = audit[
        [
            "annee",
            "Num_Acc",
            "dep",
            "com",
            "statut_appariement",
        ]
    ].sort_values(
        ["dep", "com", "Num_Acc"],
        kind="stable",
    ).reset_index(drop=True)

    counts = audit["statut_appariement"].value_counts()

    n_total = len(audit)
    n_matched = int(counts.get("matched", 0))
    n_evaluated_unmatched = int(
        counts.get("evaluated_unmatched", 0)
    )
    n_not_evaluated = int(counts.get("not_evaluated", 0))
    n_evaluated = n_matched + n_evaluated_unmatched

    if (
        n_matched
        + n_evaluated_unmatched
        + n_not_evaluated
        != n_total
    ):
        raise ValueError(
            "Les statuts d'audit ne partitionnent pas "
            "exactement les accidents BAAC."
        )

    summary = (
        audit.groupby(
            ["dep", "statut_appariement"],
            dropna=False,
        )
        .size()
        .unstack(fill_value=0)
        .reindex(
            columns=[
                "matched",
                "evaluated_unmatched",
                "not_evaluated",
            ],
            fill_value=0,
        )
        .reset_index()
    )

    for column in (
        "matched",
        "evaluated_unmatched",
        "not_evaluated",
    ):
        if column not in summary:
            summary[column] = 0

    summary["total_baac"] = (
        summary["matched"]
        + summary["evaluated_unmatched"]
        + summary["not_evaluated"]
    )
    summary["taux_matching_total_pct"] = (
        100 * summary["matched"] / summary["total_baac"]
    ).round(2)
    summary["taux_matching_evalues_pct"] = (
        100
        * summary["matched"]
        / (
            summary["matched"]
            + summary["evaluated_unmatched"]
        )
    ).round(2)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    audit_path = (
        args.output_dir
        / f"global-match-status-{args.year}.csv"
    )
    summary_path = (
        args.output_dir
        / f"global-match-summary-{args.year}.csv"
    )

    if audit_path.exists() or summary_path.exists():
        existing = [
            str(path)
            for path in (audit_path, summary_path)
            if path.exists()
        ]
        raise FileExistsError(
            "Sortie déjà présente :\n- "
            + "\n- ".join(existing)
            + "\nSupprime-la explicitement avant de recalculer."
        )

    audit.to_csv(audit_path, index=False)
    summary.to_csv(summary_path, index=False)

    print(f"Année BAAC : {args.year}")
    print(f"Fichiers de candidats : {len(candidate_files):,}")
    print(f"Accidents BAAC géolocalisés : {n_total:,}")
    print(f"Accidents présents dans ≥ 1 fichier : {n_evaluated:,}")
    print(f"Accidents appariés dans ≥ 1 région : {n_matched:,}")
    print(
        "Évalués, mais sans candidat dans aucune région : "
        f"{n_evaluated_unmatched:,}"
    )
    print(
        "Absents de tous les fichiers de candidats : "
        f"{n_not_evaluated:,}"
    )
    print(
        "Taux d'appariement sur BAAC géolocalisé : "
        f"{n_matched / n_total:.2%}"
    )

    if n_evaluated:
        print(
            "Taux d'appariement parmi les accidents évalués : "
            f"{n_matched / n_evaluated:.2%}"
        )

    print("\nRésumé par département :")
    print(summary.to_string(index=False))

    print(f"\nAudit détaillé : {audit_path}")
    print(f"Synthèse départementale : {summary_path}")


if __name__ == "__main__":
    main()