from __future__ import annotations

"""H9 — analyse descriptive comparative des sorties de analyse_h9_modes_support.py.

Exécution depuis la racine du projet :
  uv run python src/analyse_h9_resultats.py \
      --input-dir outputs/h9_modes \
      --start-year 2019 --end-year 2025 \
      --output-dir outputs/h9_modes_analyse

L'année de fin est exclue. Le script :
- agrège les résultats annuels EDPM/vélo ;
- calcule proportions, différences et rapports de risques descriptifs ;
- produit les mêmes comparaisons par VMA ;
- compare les modes dans les cellules communes du fichier détail ;
- standardise les taux par contexte BAAC sur une pondération commune ;
- exporte les tableaux CSV nécessaires à la rédaction de H9.

Aucune conclusion causale ni estimation d'exposition n'est produite.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


MODES = ["edpm", "velo"]
OUTCOMES = [
    "support_compatible",
    "sans_collision_baac",
    "obs_fixe",
    "obs_bordure",
    "sortie_chaussee",
    "surface_adverse",
    "manoeuvre_evitement",
    "obs_mobile_vehicule",
]


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2025, help="Année exclue")
    return parser.parse_args()


def read_result(input_dir, stem, suffix):
    path = input_dir / f"h9_support_par_mode_{stem}_{suffix}.csv"
    if not path.is_file():
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    required = {"annee", "zone", "mode", "accidents_mode", *OUTCOMES}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path}: colonnes absentes {sorted(missing)}")
    return df


def add_rates(df):
    result = df.copy()
    for outcome in OUTCOMES:
        result[f"part_{outcome}"] = np.where(
            result["accidents_mode"].gt(0),
            result[outcome] / result["accidents_mode"],
            np.nan,
        )
    return result


def compare_modes(df, strata):
    """Met EDPM et vélo en vis-à-vis dans les mêmes strates."""

    keys = list(strata)
    value_cols = ["accidents_mode", *OUTCOMES]

    base = df[keys + ["mode", *value_cols]].copy()

    edpm = (
        base.loc[base["mode"].eq("edpm"), keys + value_cols]
        .groupby(keys, dropna=False, as_index=False)[value_cols]
        .sum()
        .rename(
            columns={
                column: f"{column}_edpm"
                for column in value_cols
            }
        )
    )

    velo = (
        base.loc[base["mode"].eq("velo"), keys + value_cols]
        .groupby(keys, dropna=False, as_index=False)[value_cols]
        .sum()
        .rename(
            columns={
                column: f"{column}_velo"
                for column in value_cols
            }
        )
    )

    both = edpm.merge(
        velo,
        on=keys,
        how="inner",
        validate="one_to_one",
    )

    both = both.loc[
        both["accidents_mode_edpm"].gt(0)
        & both["accidents_mode_velo"].gt(0)
    ].copy()

    for outcome in OUTCOMES:
        edpm_rate = (
            both[f"{outcome}_edpm"]
            / both["accidents_mode_edpm"]
        )

        velo_rate = (
            both[f"{outcome}_velo"]
            / both["accidents_mode_velo"]
        )

        both[f"part_{outcome}_edpm"] = edpm_rate
        both[f"part_{outcome}_velo"] = velo_rate

        both[f"difference_points_{outcome}"] = (
            100 * (edpm_rate - velo_rate)
        )

        both[f"rr_{outcome}"] = np.where(
            velo_rate.gt(0),
            edpm_rate / velo_rate,
            np.nan,
        )

    return both


def aggregate_comparison(df, strata):
    """Agrège les observations en conservant le mode, puis compare EDPM/vélo."""

    keys = list(strata)

    grouped = (
        df.groupby(
            keys + ["mode"],
            dropna=False,
            as_index=False,
        )[["accidents_mode", *OUTCOMES]]
        .sum()
    )

    return compare_modes(grouped, keys)


def standardize_common_context(detail):
    context = [
        "annee",
        "zone",
        "vma_classe",
        "situation_classe",
        "surface_classe",
    ]
    compared = compare_modes(detail, context)
    if compared.empty:
        return pd.DataFrame()

    compared["poids_commun"] = (
        compared["accidents_mode_edpm"] + compared["accidents_mode_velo"]
    )
    compared["poids_commun"] = (
        compared["poids_commun"] / compared["poids_commun"].sum()
    )

    rows = []
    for outcome in OUTCOMES:
        edpm_rate = compared[f"part_{outcome}_edpm"]
        velo_rate = compared[f"part_{outcome}_velo"]
        weight = compared["poids_commun"]
        p_edpm = (edpm_rate * weight).sum()
        p_velo = (velo_rate * weight).sum()
        rows.append(
            {
                "outcome": outcome,
                "cellules_communes": len(compared),
                "accidents_edpm_cellules_communes": int(compared["accidents_mode_edpm"].sum()),
                "accidents_velo_cellules_communes": int(compared["accidents_mode_velo"].sum()),
                "taux_standardise_edpm": p_edpm,
                "taux_standardise_velo": p_velo,
                "difference_points": 100 * (p_edpm - p_velo),
                "rr_standardise": p_edpm / p_velo if p_velo > 0 else np.nan,
            }
        )
    return pd.DataFrame(rows)


def main():
    args = arguments()
    if args.start_year >= args.end_year:
        raise ValueError("Période invalide")

    suffix = f"{args.start_year}_{args.end_year}"
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    annual = read_result(input_dir, "annuel", suffix)
    vma = read_result(input_dir, "vma", suffix)
    vma_unique = read_result(input_dir, "vma_unique", suffix)
    detail = read_result(input_dir, "detail", suffix)

    annual_rates = add_rates(annual)
    annual_summary = aggregate_comparison(annual, ["zone"])
    annual_by_year = aggregate_comparison(annual, ["annee", "zone"])

    vma_summary = aggregate_comparison(vma, ["zone", "vma_classe"])
    vma_by_year = aggregate_comparison(vma, ["annee", "zone", "vma_classe"])
    vma_unique_summary = aggregate_comparison(vma_unique, ["zone", "vma_classe"])
    vma_unique_by_year = aggregate_comparison(
        vma_unique,
        ["annee", "zone", "vma_classe"],
    )

    detail_common = compare_modes(
        detail,
        ["annee", "zone", "vma_classe", "situation_classe", "surface_classe"],
    )
    standardized = standardize_common_context(detail)

    tables = {
        "h9_taux_annuels_par_mode.csv": annual_rates,
        "h9_comparaison_annuelle_zone.csv": annual_by_year,
        "h9_comparaison_zone_total.csv": annual_summary,
        "h9_comparaison_vma_total.csv": vma_summary,
        "h9_comparaison_vma_annuelle.csv": vma_by_year,
        "h9_comparaison_vma_unique_total.csv": vma_unique_summary,
        "h9_comparaison_vma_unique_annuelle.csv": vma_unique_by_year,
        "h9_cellules_detail_communes.csv": detail_common,
        "h9_standardisation_contextes_communs.csv": standardized,
    }

    for name, frame in tables.items():
        frame.to_csv(output_dir / name, index=False)
        print(output_dir / name)

    if standardized.empty:
        print("Aucune cellule BAAC commune EDPM/vélo dans le fichier détail.")
    else:
        display = standardized.loc[
            standardized["outcome"].eq("support_compatible"),
            [
                "outcome",
                "cellules_communes",
                "taux_standardise_edpm",
                "taux_standardise_velo",
                "difference_points",
                "rr_standardise",
            ],
        ]
        print(display.to_string(index=False))


if __name__ == "__main__":
    main()
