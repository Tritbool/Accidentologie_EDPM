from __future__ import annotations

"""H0 : gravité BAAC par année, mode, zone, voie OSM et nature des tiers.

Exemple : uv run python src/analyse_gravite_h0.py --start-year 2019 --end-year 2025
Bornes [start-year, end-year[. Produit deux CSV : bilan compact et ventilation
exclusive par catégorie d'accident. Aucune lecture des CSV BAAC bruts.
"""

import argparse
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

MODES = {
    "VELO": (1, "n_velo_sans_assistance"),
    "VAE": (80, "n_vae"),
    "EDPM": (50, "n_edpm"),
    "EDP_SANS_MOTEUR": (60, "n_edp_sans_moteur"),
    "CYCLO": (2, "n_cyclo"),
}
GRAVITES = ("indemnes", "blesses_legers", "hospitalises", "tues")
ETATS = (*GRAVITES, "gravite_inconnue")
ZONES = {1: "Hors agglomération", 2: "En agglomération"}
NON_MOTORISES = {1, 60, 80}
INDETERMINES = {0, 99}
GROUPES = ["annee", "mode", "zone", "type_voie"]


def arguments() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start-year", type=int, default=2019)
    p.add_argument("--end-year", type=int, default=2025)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--output", type=Path, default=None,
                   help="Chemin du CSV compact ; le détail utilise le suffixe -par-tiers.csv")
    return p.parse_args()


def compte(df: pd.DataFrame, colonnes: list[str]) -> pd.Series:
    if not colonnes:
        return pd.Series(0, index=df.index, dtype="int64")
    return df[colonnes].sum(axis=1)


def lire_annee(root: Path, year: int) -> tuple[pd.DataFrame, list[str]]:
    path = root / "outputs" / "tables" / f"accidents-analyse-{year}.geoparquet"
    if not path.is_file():
        raise FileNotFoundError(path)
    schema = set(pq.read_schema(path).names)
    catv_cols = sorted(
        c for c in schema if c.startswith("n_catv_") and c[7:].isdigit()
    )
    required = {
        "Num_Acc", "agg", "highway", "n_vehicules", "n_pietons",
        "n_catv_inconnu", *catv_cols,
    }
    for code, prefix in MODES.values():
        required.add(f"n_catv_{code:02d}")
        required.update(f"{prefix}_{grav}" for grav in ETATS)
    missing = required - schema
    if missing:
        raise ValueError(
            f"{path}: colonnes manquantes {sorted(missing)} ; "
            "reconstruire avec le build_accident_table.py enrichi."
        )
    df = pd.read_parquet(path, columns=sorted(required))
    if df["Num_Acc"].isna().any() or df["Num_Acc"].duplicated().any():
        raise ValueError(f"{path}: Num_Acc nul ou dupliqué")
    for col in [*catv_cols, "n_catv_inconnu", "n_vehicules", "n_pietons",
                *(f"{prefix}_{g}" for _, prefix in MODES.values() for g in ETATS)]:
        values = pd.to_numeric(df[col], errors="raise")
        if values.isna().any() or values.lt(0).any() or values.ne(values.round()).any():
            raise ValueError(f"{path}: compteur invalide dans {col}")
        df[col] = values.astype("int64")
    if not df[catv_cols].sum(axis=1).add(df["n_catv_inconnu"]).eq(df["n_vehicules"]).all():
        raise ValueError(f"{path}: somme des n_catv_* différente de n_vehicules")
    df["annee"] = year
    df["zone"] = pd.to_numeric(df["agg"], errors="coerce").map(ZONES).fillna("Zone inconnue")
    df["type_voie"] = df["highway"].astype("string").fillna("Non apparié")
    return df, catv_cols


def categorie_tiers(df: pd.DataFrame, code_cible: int, catv_cols: list[str]) -> pd.Series:
    code_cols = {int(c[7:]): c for c in catv_cols}
    # Chaque n_catv_* est compté exactement une fois ; la catégorie cible
    # n'entre dans les tiers que si un second véhicule du même code existe.
    non_motorises = [col for code, col in code_cols.items()
                     if code in NON_MOTORISES and code != code_cible]
    motorises = [col for code, col in code_cols.items()
                 if code not in NON_MOTORISES | INDETERMINES | {code_cible}]
    indetermines = [col for code, col in code_cols.items() if code in INDETERMINES]
    flags = pd.DataFrame({
        "pieton": df["n_pietons"].gt(0),
        "meme_catv": df[f"n_catv_{code_cible:02d}"].gt(1),
        "non_motorise": compte(df, non_motorises).gt(0),
        "motorise": compte(df, motorises).gt(0),
        "indetermine": compte(df, indetermines).add(df["n_catv_inconnu"]).gt(0),
    }, index=df.index)
    n_flags = flags.sum(axis=1)
    labels = pd.Series("Combinaison de tiers", index=df.index, dtype="string")
    labels.loc[n_flags.eq(0)] = "Solo (sans piéton)"
    for key, label in (
        ("pieton", "Piéton seul"),
        ("meme_catv", "Autre véhicule du même catv seulement"),
        ("non_motorise", "Autre véhicule non motorisé seulement"),
        ("motorise", "Autre véhicule motorisé seulement"),
        ("indetermine", "Véhicule indéterminé seulement"),
    ):
        labels.loc[n_flags.eq(1) & flags[key]] = label
    return labels


def resumer(data: pd.DataFrame, groupes: list[str]) -> pd.DataFrame:
    rows = []
    for key, group in data.groupby(groupes, dropna=False, sort=True):
        row = dict(zip(groupes, key))
        n = len(group)
        row["accidents"] = n
        all_counts = group[[f"n_mode_{g}" for g in ETATS]].sum(axis=1)
        row["accidents_sans_usager_du_mode_documente"] = int(all_counts.eq(0).sum())
        for grav in ETATS:
            vals = group[f"n_mode_{grav}"]
            involved = int(vals.gt(0).sum())
            row[f"accidents_avec_{grav}"] = involved
            row[f"part_avec_{grav}_pct"] = round(100 * involved / n, 2)
            row[f"personnes_{grav}"] = int(vals.sum())
        rows.append(row)
    if not rows:
        raise ValueError("Aucun accident à résumer")
    return pd.DataFrame(rows).sort_values(groupes).reset_index(drop=True)


def analyser(root: Path, years: range) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames = []
    for year in years:
        df, catv_cols = lire_annee(root, year)
        for mode, (code, prefix) in MODES.items():
            cible = df.loc[df[f"n_catv_{code:02d}"].gt(0)].copy()
            if mode == "CYCLO":
                cible = cible.loc[cible["zone"].eq("Hors agglomération")].copy()
            if cible.empty:
                continue
            cible["mode"] = mode
            cible["categorie_accident"] = categorie_tiers(cible, code, catv_cols)
            for grav in ETATS:
                cible[f"n_mode_{grav}"] = cible[f"{prefix}_{grav}"]
            frames.append(cible[[*GROUPES, "categorie_accident", *(f"n_mode_{g}" for g in ETATS)]])
    if not frames:
        raise ValueError("Aucun accident pour la période demandée")
    data = pd.concat(frames, ignore_index=True)
    compact = resumer(data, GROUPES)
    detail = resumer(data, [*GROUPES, "categorie_accident"])
    total_detail = detail.groupby(GROUPES, dropna=False)["accidents"].sum().rename("detail")
    check = compact.set_index(GROUPES)["accidents"].rename("compact").to_frame().join(total_detail)
    if check["detail"].isna().any() or not check["compact"].eq(check["detail"]).all():
        raise ValueError("Les catégories de tiers ne reconstituent pas les totaux")
    return compact, detail


def main() -> None:
    args = arguments()
    if args.start_year >= args.end_year:
        raise ValueError("start-year doit être inférieur à end-year")
    root = args.root.resolve()
    output = args.output or root / "outputs" / "tables" / f"gravite_H0_{args.start_year}_{args.end_year}.csv"
    detail_path = output.with_name(f"{output.stem}-par-tiers{output.suffix}")
    for path in (output, detail_path):
        if path.exists():
            raise FileExistsError(f"Sortie déjà présente : {path}")
    compact, detail = analyser(root, range(args.start_year, args.end_year))
    output.parent.mkdir(parents=True, exist_ok=True)
    compact.to_csv(output, index=False, sep=";")
    detail.to_csv(detail_path, index=False, sep=";")
    print(f"H0 compact : {len(compact)} lignes → {output}")
    print(f"H0 par tiers : {len(detail)} lignes → {detail_path}")


if __name__ == "__main__":
    main()
