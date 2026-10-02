from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostique le changement de granularité BAAC de la table "
            "'lieux' entre deux années."
        )
    )

    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Racine du projet",
    )

    parser.add_argument(
        "--years",
        type=int,
        nargs=2,
        default=[2022, 2023],
        metavar=("YEAR_A", "YEAR_B"),
        help="Deux années à comparer, par défaut 2022 2023",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Répertoire de sortie ; défaut outputs/diagnostic_lieux",
    )

    return parser.parse_args()


def normalize_num_acc(values: pd.Series) -> pd.Series:
    values = (
        values.astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )

    if values.isna().any() or values.eq("").any():
        raise ValueError("Num_Acc vide ou manquant")

    return values


def numeric_or_na(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce")


def read_lieux(root: Path, year: int) -> pd.DataFrame:
    path = (
        root
        / "BAAC"
        / "derived"
        / str(year)
        / f"lieux-{year}.parquet"
    )

    if not path.is_file():
        raise FileNotFoundError(path)

    schema = pq.read_schema(path)

    print(f"\n=== {year} ===")
    print(f"Fichier : {path}")
    print(f"Lignes Parquet : {pq.ParquetFile(path).metadata.num_rows:,}")
    print("Schéma :")

    for field in schema:
        print(f"  - {field.name}: {field.type}")

    df = pd.read_parquet(path)

    if "Num_Acc" not in df.columns:
        raise ValueError(f"{path}: Num_Acc absent")

    df["Num_Acc"] = normalize_num_acc(df["Num_Acc"])

    print(f"Colonnes Pandas : {len(df.columns)}")
    print(f"Lignes lues : {len(df):,}")
    print(f"Accidents distincts : {df['Num_Acc'].nunique():,}")
    print(f"Lignes supplémentaires : {len(df) - df['Num_Acc'].nunique():,}")

    return df


def value_profile(
    df: pd.DataFrame,
    year: int,
    columns: list[str],
) -> pd.DataFrame:
    rows = []

    for column in columns:
        if column not in df.columns:
            rows.append(
                {
                    "annee": year,
                    "colonne": column,
                    "presente": False,
                    "dtype": pd.NA,
                    "non_nuls": pd.NA,
                    "valeurs_distinctes": pd.NA,
                    "top_valeurs": pd.NA,
                }
            )
            continue

        values = df[column]

        top = (
            values
            .astype("string")
            .fillna("[NA]")
            .value_counts(dropna=False)
            .head(15)
        )

        rows.append(
            {
                "annee": year,
                "colonne": column,
                "presente": True,
                "dtype": str(values.dtype),
                "non_nuls": int(values.notna().sum()),
                "valeurs_distinctes": int(values.nunique(dropna=True)),
                "top_valeurs": " | ".join(
                    f"{index}:{count}"
                    for index, count in top.items()
                ),
            }
        )

    return pd.DataFrame(rows)


def multiplicity_profile(
    df: pd.DataFrame,
    year: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    per_accident = (
        df
        .groupby("Num_Acc", sort=False)
        .size()
        .rename("n_lignes_lieux")
        .reset_index()
    )

    distribution = (
        per_accident["n_lignes_lieux"]
        .value_counts()
        .sort_index()
        .rename_axis("n_lignes_lieux")
        .reset_index(name="n_accidents")
    )

    distribution.insert(0, "annee", year)

    print("\nNombre de lignes 'lieux' par accident :")
    print(distribution.to_string(index=False))

    return per_accident, distribution


def conflict_profile(
    df: pd.DataFrame,
    year: int,
    columns: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    available = [
        column
        for column in columns
        if column in df.columns
    ]

    if not available:
        return pd.DataFrame(), pd.DataFrame()

    distinct = (
        df
        .groupby("Num_Acc", sort=False)[available]
        .nunique(dropna=True)
    )

    per_column = pd.DataFrame(
        {
            "annee": year,
            "colonne": available,
            "accidents_avec_valeur_renseignee": [
                int(distinct[column].gt(0).sum())
                for column in available
            ],
            "accidents_ambigus": [
                int(distinct[column].gt(1).sum())
                for column in available
            ],
            "part_ambigue_parmi_renseignes_pct": [
                round(
                    100
                    * distinct[column].gt(1).sum()
                    / distinct[column].gt(0).sum(),
                    3,
                )
                if distinct[column].gt(0).sum()
                else pd.NA
                for column in available
            ],
            "maximum_valeurs_distinctes": [
                int(distinct[column].max())
                for column in available
            ],
        }
    )

    flags = distinct.gt(1).rename(
        columns={
            column: f"{column}_ambigu"
            for column in available
        }
    )

    flags["n_variables_ambigues"] = flags.sum(axis=1)

    combined = (
        flags
        .groupby("n_variables_ambigues")
        .size()
        .rename("n_accidents")
        .reset_index()
    )

    combined.insert(0, "annee", year)

    print("\nAmbiguïtés par variable :")
    print(per_column.to_string(index=False))

    print("\nNombre de variables de lieu ambiguës par accident :")
    print(combined.to_string(index=False))

    return per_column, combined


def examples_of_conflicts(
    df: pd.DataFrame,
    per_accident: pd.DataFrame,
    year: int,
    columns: list[str],
    limit: int = 30,
) -> pd.DataFrame:
    available = [
        column
        for column in columns
        if column in df.columns
    ]

    if not available:
        return pd.DataFrame()

    n_unique = (
        df
        .groupby("Num_Acc", sort=False)[available]
        .nunique(dropna=True)
    )

    ambiguous_ids = n_unique.index[
        n_unique.gt(1).any(axis=1)
    ]

    if len(ambiguous_ids) == 0:
        return pd.DataFrame()

    n_variables = n_unique.loc[
        ambiguous_ids
    ].gt(1).sum(axis=1)

    selected_ids = (
        n_variables
        .sort_values(ascending=False)
        .head(limit)
        .index
    )

    examples = df.loc[
        df["Num_Acc"].isin(selected_ids),
        ["Num_Acc", *available],
    ].copy()

    examples = examples.merge(
        per_accident,
        on="Num_Acc",
        how="left",
        validate="many_to_one",
    )

    examples.insert(0, "annee", year)

    examples = examples.sort_values(
        ["n_lignes_lieux", "Num_Acc"],
        ascending=[False, True],
        kind="stable",
    )

    return examples


def compare_schemas(
    tables: dict[int, pd.DataFrame],
) -> pd.DataFrame:
    years = sorted(tables)

    all_columns = sorted(
        set().union(
            *(
                set(df.columns)
                for df in tables.values()
            )
        )
    )

    rows = []

    for column in all_columns:
        row = {"colonne": column}

        for year in years:
            df = tables[year]

            row[f"present_{year}"] = column in df.columns

            row[f"dtype_{year}"] = (
                str(df[column].dtype)
                if column in df.columns
                else pd.NA
            )

        rows.append(row)

    return pd.DataFrame(rows)


def main() -> None:
    args = arguments()

    root = args.root.resolve()

    year_a, year_b = args.years

    if year_a == year_b:
        raise ValueError("Les deux années doivent être distinctes")

    outdir = args.output_dir or (
        root / "outputs" / "diagnostic_lieux"
    )

    outdir = outdir.resolve()

    outputs = {
        "schema": outdir / f"schema_lieux_{year_a}_{year_b}.csv",
        "profiles": outdir / f"profil_colonnes_lieux_{year_a}_{year_b}.csv",
        "multiplicity": outdir / f"multiplicite_lieux_{year_a}_{year_b}.csv",
        "conflicts": outdir / f"ambiguite_lieux_{year_a}_{year_b}.csv",
        "combined": outdir / f"ambiguite_combinee_lieux_{year_a}_{year_b}.csv",
        "examples_a": outdir / f"exemples_ambigu_lieux_{year_a}.csv",
        "examples_b": outdir / f"exemples_ambigu_lieux_{year_b}.csv",
    }

    existing = [
        str(path)
        for path in outputs.values()
        if path.exists()
    ]

    if existing:
        raise FileExistsError(
            "Sorties déjà présentes : "
            + ", ".join(existing)
        )

    tables = {
        year_a: read_lieux(root, year_a),
        year_b: read_lieux(root, year_b),
    }

    diagnostic_columns = [
        "catr",
        "voie",
        "v1",
        "v2",
        "circ",
        "nbv",
        "vosp",
        "prof",
        "plan",
        "lartpc",
        "larrout",
        "surf",
        "infra",
        "situ",
        "vma",
    ]

    schema = compare_schemas(tables)

    profiles = []
    multiplicities = []
    conflicts = []
    combined_conflicts = []
    examples = {}

    for year, df in tables.items():
        per_accident, multiplicity = multiplicity_profile(df, year)

        profile = value_profile(
            df,
            year,
            diagnostic_columns,
        )

        conflict, combined = conflict_profile(
            df,
            year,
            diagnostic_columns,
        )

        profiles.append(profile)
        multiplicities.append(multiplicity)
        conflicts.append(conflict)
        combined_conflicts.append(combined)

        examples[year] = examples_of_conflicts(
            df,
            per_accident,
            year,
            diagnostic_columns,
        )

    outdir.mkdir(parents=True, exist_ok=True)

    schema.to_csv(
        outputs["schema"],
        index=False,
    )

    pd.concat(
        profiles,
        ignore_index=True,
    ).to_csv(
        outputs["profiles"],
        index=False,
    )

    pd.concat(
        multiplicities,
        ignore_index=True,
    ).to_csv(
        outputs["multiplicity"],
        index=False,
    )

    pd.concat(
        conflicts,
        ignore_index=True,
    ).to_csv(
        outputs["conflicts"],
        index=False,
    )

    pd.concat(
        combined_conflicts,
        ignore_index=True,
    ).to_csv(
        outputs["combined"],
        index=False,
    )

    examples[year_a].to_csv(
        outputs["examples_a"],
        index=False,
    )

    examples[year_b].to_csv(
        outputs["examples_b"],
        index=False,
    )

    print("\n=== Sorties ===")

    for path in outputs.values():
        print(path)


if __name__ == "__main__":
    main()