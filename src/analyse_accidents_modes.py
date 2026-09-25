"""Table BAAC 2019–2024 par mode, zone, voie OSM, tiers et gravité.

Exécution depuis la racine du projet :
    python analyse_accidents_modes.py --mode EDPM
    python analyse_accidents_modes.py --mode VELO --out outputs/tables/velo.csv
    python analyse_accidents_modes.py --mode VAE --years 2022 2023 2024

Données attendues : outputs/tables/accidents-analyse-AAAA.geoparquet et
BAAC/derived/AAAA/usagers-AAAA.parquet (ou BAAC/raw/AAAA/usagers-AAAA.csv).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

MODES = {
    "EDPM": (50, "EDPM"),
    "VELO": (1, "vélo"),
    "VAE": (80, "VAE"),
    "EDP_SANS_MOTEUR": (60, "EDP sans moteur"),
}
CATV = {
    0: "Indéterminable", 1: "Bicyclette", 2: "Cyclomoteur < 50 cm³",
    3: "Voiturette", 7: "Voiture légère", 10: "Véhicule utilitaire",
    13: "PL ≤ 7,5 t", 14: "PL > 7,5 t", 15: "PL avec remorque",
    16: "Tracteur routier", 17: "Tracteur avec semi-remorque",
    20: "Engin spécial", 21: "Tracteur agricole", 30: "Scooter < 50 cm³",
    31: "Moto ≤ 125 cm³", 32: "Scooter ≤ 125 cm³",
    33: "Moto > 125 cm³", 34: "Scooter > 125 cm³",
    35: "Quad léger", 36: "Quad lourd", 37: "Autobus", 38: "Autocar",
    39: "Train", 40: "Tramway", 41: "Trois-roues ≤ 50 cm³",
    42: "Trois-roues ≤ 125 cm³", 43: "Trois-roues > 125 cm³",
    50: "EDPM", 60: "EDP sans moteur", 80: "VAE", 99: "Autre véhicule",
}
SEVERITES = {
    2: "tuées", 3: "blessées hospitalisées", 4: "blessées légères",
    1: "indemnes",
}


def normalize_id(s: pd.Series) -> pd.Series:
    """Num_Acc est entier ; évite une jointure float/string silencieusement vide."""
    x = pd.to_numeric(s, errors="coerce")
    if x.isna().any() or ((x % 1) != 0).any():
        raise ValueError("Num_Acc contient des identifiants manquants ou non entiers")
    return x.astype("int64")


def find_usagers(root: Path, year: int) -> Path:
    choices = [
        root / "BAAC" / "derived" / str(year) / f"usagers-{year}.parquet",
        root / "BAAC" / "raw" / str(year) / f"usagers-{year}.csv",
    ]
    for path in choices:
        if path.is_file():
            return path
    raise FileNotFoundError(f"Usagers {year} introuvables : {choices}")


def load_usagers(root: Path, year: int) -> pd.DataFrame:
    path = find_usagers(root, year)
    cols = ["Num_Acc", "id_usager", "id_vehicule", "catu", "grav"]
    if path.suffix == ".parquet":
        available = pq.read_schema(path).names
        needed = [c for c in cols if c in available]
        if "Num_Acc" not in needed or "grav" not in needed or "id_vehicule" not in needed:
            raise ValueError(f"Colonnes usagers indispensables manquantes : {path}")
        u = pd.read_parquet(path, columns=needed)
    else:
        header = pd.read_csv(path, sep=";", nrows=0).columns
        needed = [c for c in cols if c in header]
        if not {"Num_Acc", "grav", "id_vehicule"}.issubset(needed):
            raise ValueError(f"Colonnes usagers indispensables manquantes : {path}")
        u = pd.read_csv(path, sep=";", usecols=needed, low_memory=False)
    u["Num_Acc"] = normalize_id(u["Num_Acc"])
    if "id_usager" in u and u["id_usager"].duplicated().any():
        raise ValueError(f"id_usager dupliqué : {path}")
    u["grav"] = pd.to_numeric(u["grav"], errors="coerce")
    if "catu" in u:
        u["catu"] = pd.to_numeric(u["catu"], errors="coerce")
    return u


def load_vehicules(root: Path, year: int) -> pd.DataFrame:
    choices = [
        root / "BAAC" / "derived" / str(year) / f"vehicules-{year}.parquet",
        root / "BAAC" / "raw" / str(year) / f"vehicules-{year}.csv",
    ]
    path = next((p for p in choices if p.is_file()), None)
    if path is None:
        raise FileNotFoundError(f"Véhicules {year} introuvables : {choices}")
    cols = ["Num_Acc", "id_vehicule", "catv"]
    if path.suffix == ".parquet":
        v = pd.read_parquet(path, columns=cols)
    else:
        v = pd.read_csv(path, sep=";", usecols=cols, low_memory=False)
    v["Num_Acc"] = normalize_id(v["Num_Acc"])
    v["catv"] = pd.to_numeric(v["catv"], errors="coerce")
    if v.duplicated(["Num_Acc", "id_vehicule"]).any():
        raise ValueError(f"Véhicule dupliqué dans {path}")
    return v


def load_year(root: Path, year: int, mode_code: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    path = root / "outputs" / "tables" / f"accidents-analyse-{year}.geoparquet"
    if not path.is_file():
        raise FileNotFoundError(path)
    names = pq.read_schema(path).names
    cat_cols = sorted(c for c in names if c.startswith("n_catv_") and c != "n_catv_inconnu")
    target = f"n_catv_{mode_code:02d}"
    required = {"Num_Acc", "agg", "highway", "n_vehicules", "n_catv_inconnu", target}
    if not required.issubset(names):
        raise ValueError(f"Colonnes manquantes dans {path}: {sorted(required - set(names))}")
    a = pd.read_parquet(path, columns=sorted(required | set(cat_cols)))
    a["Num_Acc"] = normalize_id(a["Num_Acc"])
    if a["Num_Acc"].duplicated().any():
        raise ValueError(f"Num_Acc dupliqué dans {path}")
    a = a.loc[a[target].gt(0)].copy()
    a["annee"] = year
    a["zone"] = pd.to_numeric(a["agg"], errors="coerce").map(
        {1: "Hors agglomération", 2: "En agglomération"}
    ).fillna("Zone inconnue")
    a["type_voie"] = a["highway"].astype("string").fillna("Non apparié")
    v = load_vehicules(root, year)
    mode_vehicles = v.loc[v["catv"].eq(mode_code), ["Num_Acc", "id_vehicule"]]
    if mode_vehicles.empty and not a.empty:
        raise ValueError(f"Aucun véhicule du mode {mode_code} dans BAAC {year}")
    u = load_usagers(root, year)
    if "catu" in u:
        u = u.loc[u["catu"].isin([1, 2])].copy()  # Piétons rattachés au véhicule heurtant : exclus.
    m = u.merge(mode_vehicles.assign(_mode=True), on=["Num_Acc", "id_vehicule"],
                how="inner", validate="many_to_one")
    m = m.loc[m["Num_Acc"].isin(a["Num_Acc"])].copy()
    m["annee"] = year
    return a, m


def make_tables(root: Path, years: list[int], mode: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    code, label = MODES[mode]
    loaded = [load_year(root, y, code) for y in years]
    a = pd.concat([part[0] for part in loaded], ignore_index=True)
    u = pd.concat([part[1] for part in loaded], ignore_index=True)
    if a.empty:
        raise ValueError(f"Aucun accident {label} pour {years}")
    keys = ["annee", "Num_Acc"]
    if a.duplicated(keys).any():
        raise ValueError("Accidents dupliqués après concaténation")
    if u.empty:
        raise ValueError("Aucun usager rattaché aux véhicules ciblés")
    group_keys = ["zone", "type_voie"]
    a["Sans autre véhicule"] = pd.to_numeric(a["n_vehicules"], errors="coerce").eq(1)
    a[f"Autre {label}"] = pd.to_numeric(a[f"n_catv_{code:02d}"], errors="coerce").gt(1)
    a["Catégorie véhicule inconnue"] = pd.to_numeric(a["n_catv_inconnu"], errors="coerce").gt(0)
    cat_cols = sorted(c for c in a if c.startswith("n_catv_") and c != "n_catv_inconnu")
    for col in cat_cols:
        num = int(col.removeprefix("n_catv_"))
        if num != code:
            a[f"Avec {CATV.get(num, f'catv {num:02d}')} [catv {num:02d}]"] = pd.to_numeric(
                a[col], errors="coerce"
            ).gt(0)
    flag_cols = ["Sans autre véhicule", f"Autre {label}", "Catégorie véhicule inconnue"] + [
        c for c in a if c.startswith("Avec ") and "[catv " in c
    ]
    flags = a.groupby(group_keys, dropna=False)[flag_cols].sum().astype(int)
    n = a.groupby(group_keys, dropna=False).size().rename(f"Accidents {label}")

    # Les personnes sont comptées uniquement sur les véhicules du mode étudié.
    # On sépare victimes (personnes) et accidents ayant >= 1 victime de chaque gravité.
    u = u.assign(**{
        f"Personnes {name}": u["grav"].eq(grav).astype(int)
        for grav, name in SEVERITES.items()
    })
    u["Personnes gravité inconnue"] = ~u["grav"].isin(SEVERITES)
    u["Personnes gravité inconnue"] = u["Personnes gravité inconnue"].astype(int)
    person_cols = [f"Personnes {s}" for s in SEVERITES.values()] + ["Personnes gravité inconnue"]
    per_acc = u.groupby(keys, as_index=False)[person_cols].sum()
    per_acc["Usagers du mode documentés"] = per_acc[person_cols].sum(axis=1)
    for grav, name in SEVERITES.items():
        per_acc[f"Accidents avec ≥1 {name}"] = per_acc[f"Personnes {name}"].gt(0).astype(int)
    per_acc["Accidents avec ≥1 tué ou hospitalisé"] = (
        per_acc["Accidents avec ≥1 tuées"].gt(0)
        | per_acc["Accidents avec ≥1 blessées hospitalisées"].gt(0)
    ).astype(int)
    per_acc["Accidents sans usager documenté"] = 0
    a = a.merge(per_acc, on=keys, how="left", validate="one_to_one")
    missing = a["Usagers du mode documentés"].isna()
    severity_cols = [c for c in per_acc if c.startswith("Accidents avec ≥1")]
    a["Accidents sans usager documenté"] = missing.astype(int)
    a[person_cols + severity_cols + ["Usagers du mode documentés"]] = a[
        person_cols + severity_cols + ["Usagers du mode documentés"]
    ].fillna(0).astype(int)
    severity_flags = severity_cols + ["Accidents sans usager documenté"]
    counts = pd.concat([
        n,
        a.groupby(group_keys, dropna=False)[severity_flags + person_cols + ["Usagers du mode documentés"]].sum(),
        flags,
    ], axis=1).reset_index()
    counts = counts.sort_values(["zone", f"Accidents {label}"],
                                ascending=[True, False]).reset_index(drop=True)
    counts = counts.loc[:, [c for c in counts if c in group_keys or counts[c].sum() > 0]]
    readable = counts.copy()
    denom = counts[f"Accidents {label}"]
    accident_cols = [c for c in counts if c.startswith("Accidents avec ≥1") or c.startswith("Accidents sans usager")]
    for col in severity_flags + flag_cols:
        if col not in counts:
            continue
        readable[col] = [f"{int(x)} ({100 * x / d:.1f} %)" for x, d in zip(counts[col], denom)]
    return counts, readable


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES, default="EDPM")
    parser.add_argument("--years", type=int, nargs="+", default=list(range(2019, 2025)))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.name == "notebooks":
        root = root.parent
    counts, readable = make_tables(root, args.years, args.mode)
    out = args.out or root / "outputs" / "tables" / f"bilan-{args.mode.lower()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    readable.to_csv(out, index=False, sep=";")
    counts.to_csv(out.with_name(out.stem + "-effectifs.csv"), index=False, sep=";")
    with pd.option_context("display.max_columns", None, "display.width", 250):
        print(readable.to_string(index=False))
    print(f"\nTable lisible : {out}\nEffectifs bruts : {out.with_name(out.stem + '-effectifs.csv')}")


if __name__ == "__main__":
    main()
