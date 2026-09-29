from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BAAC_ROOT = ROOT / "BAAC" / "derived"
MATCH_ROOT = ROOT / "outputs" / "maps"
OUTPUT_ROOT = ROOT / "outputs" / "tables"
YEARS = range(2019, 2025)

CATV_CODES = (
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
    15, 16, 17, 18, 19, 20, 21, 30, 31, 32, 33, 34,
    35, 36, 37, 38, 39, 40, 41, 42, 43, 50, 60, 80, 99,
)
CATV_COLUMNS = [f"n_catv_{code:02d}" for code in CATV_CODES]
GRAV_CODES = {1: "indemnes", 2: "tues", 3: "hospitalises", 4: "blesses_legers"}
# n_velo_* historique reste l'agrégat 01+80. velo_sans_assistance et vae sont disjoints.
MODES = {
    "edpm": (50,),
    "velo": (1, 80),
    "velo_sans_assistance": (1,),
    "vae": (80,),
    "edp_sans_moteur": (60,),
    "cyclo": (2,),
}
KEYS_VEH = ["Num_Acc", "id_vehicule"]


def normalize_num_acc(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    invalid = series.notna() & values.isna()
    invalid |= values.notna() & values.ne(values.round())
    if invalid.any():
        raise ValueError(f"Num_Acc non entier : {series.loc[invalid].head().tolist()}")
    return values.astype("Int64").astype("string")


def normalize_vehicle_id(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.replace(r"\s+", "", regex=True)
        .str.replace(r"\.0$", "", regex=True)
    )


def read_baac(year: int, name: str, columns: list[str]) -> pd.DataFrame:
    path = BAAC_ROOT / str(year) / f"{name}-{year}.parquet"
    if not path.is_file():
        raise FileNotFoundError(path)
    df = pd.read_parquet(path, columns=columns)
    df["Num_Acc"] = normalize_num_acc(df["Num_Acc"])
    if df["Num_Acc"].isna().any():
        raise ValueError(f"{path}: Num_Acc manquant")
    return df


def read_matches(year: int) -> gpd.GeoDataFrame:
    paths = sorted((MATCH_ROOT / str(year)).glob("candidates-*.geoparquet"))
    if not paths:
        raise FileNotFoundError(f"Aucun candidates-*.geoparquet dans {MATCH_ROOT / year}")
    frames = [
        gpd.read_parquet(
            path,
            columns=[
                "Num_Acc", "geometry", "osm_way_id", "highway",
                "distance_m", "match_status", "osm_source",
            ],
        ) for path in paths
    ]
    crs = frames[0].crs
    if crs is None or any(f.crs != crs for f in frames):
        raise ValueError(f"{year} : CRS absents ou incohérents dans les candidats")
    matches = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=crs)
    matches["Num_Acc"] = normalize_num_acc(matches["Num_Acc"])
    if matches["Num_Acc"].isna().any():
        raise ValueError(f"{year} : match sans Num_Acc")
    statuses = set(matches["match_status"].dropna().unique())
    if not statuses <= {"matched", "unmatched"} or matches["match_status"].isna().any():
        raise ValueError(f"{year} : statuts de matching inattendus : {statuses}")
    matches["_matched"] = matches["match_status"].eq("matched")
    matches["distance_m"] = pd.to_numeric(matches["distance_m"], errors="coerce")
    if matches.loc[matches["_matched"], "distance_m"].isna().any():
        raise ValueError(f"{year} : match sans distance_m")
    matches = (
        matches.sort_values(
            ["Num_Acc", "_matched", "distance_m", "osm_way_id", "osm_source"],
            ascending=[True, False, True, True, True], na_position="last", kind="stable",
        )
        .drop_duplicates("Num_Acc")
        .drop(columns="_matched")
        .reset_index(drop=True)
    )
    if not matches["Num_Acc"].is_unique:
        raise ValueError(f"{year} : matches non uniques")
    return gpd.GeoDataFrame(matches, geometry="geometry", crs=crs)


def build_year(year: int) -> None:
    print(f"\n=== {year} ===")
    matches = read_matches(year)
    caract = read_baac(
        year, "caracteristiques",
        ["Num_Acc", "jour", "mois", "an", "hrmn", "dep", "com", "agg", "int", "lum", "atm", "col"],
    )
    veh = read_baac(year, "vehicules", ["Num_Acc", "id_vehicule", "catv"])
    usagers = read_baac(year, "usagers", ["Num_Acc", "id_vehicule", "catu", "grav"])
    if caract["Num_Acc"].duplicated().any():
        raise ValueError(f"{year} : caractéristiques non uniques")
    for frame in (veh, usagers):
        frame["id_vehicule"] = normalize_vehicle_id(frame["id_vehicule"])
        if frame["id_vehicule"].isna().any():
            raise ValueError(f"{year} : id_vehicule manquant")
    if veh.duplicated(KEYS_VEH).any():
        raise ValueError(f"{year} : clé véhicule dupliquée")
    for frame, col in ((veh, "catv"), (usagers, "catu"), (usagers, "grav")):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("Int64")
    ids = matches["Num_Acc"]
    for label, frame in (("caractéristiques", caract), ("véhicules", veh), ("usagers", usagers)):
        found = int(ids.isin(frame["Num_Acc"]).sum())
        print(f"  Jointure {label} : {found:,}/{len(ids):,} accidents retrouvés")
        if found != len(ids):
            raise ValueError(f"{year} : accidents absents de {label}")
    linked = usagers.merge(veh, on=KEYS_VEH, how="left", indicator=True, validate="many_to_one")
    orphans = int(linked["_merge"].ne("both").sum())
    print(f"  Jointure usagers → véhicules : {len(linked)-orphans:,}/{len(linked):,}")
    if orphans:
        print(f"  {orphans:,} usagers sans véhicule conservés dans les totaux, non imputés à un mode")
    linked = linked.drop(columns="_merge")

    unknown_catv = veh["catv"].isna() | ~veh["catv"].isin(CATV_CODES)
    veh_counts = veh.groupby("Num_Acc").size().rename("n_vehicules")
    catv_counts = pd.crosstab(veh["Num_Acc"], veh["catv"]).reindex(columns=CATV_CODES, fill_value=0)
    catv_counts.columns = CATV_COLUMNS
    n_unknown = unknown_catv.groupby(veh["Num_Acc"]).sum().rename("n_catv_inconnu")
    veh_summary = pd.concat([veh_counts, catv_counts, n_unknown], axis=1).reset_index()
    if not (veh_summary[CATV_COLUMNS].sum(axis=1) + veh_summary["n_catv_inconnu"]).eq(veh_summary["n_vehicules"]).all():
        raise ValueError(f"{year} : catégories véhicules ne couvrent pas tous les véhicules")

    user_summary = usagers.groupby("Num_Acc").size().rename("n_usagers").to_frame()
    user_summary["n_pietons"] = usagers["catu"].eq(3).groupby(usagers["Num_Acc"]).sum()
    for code, label in GRAV_CODES.items():
        user_summary[f"n_{label}"] = usagers["grav"].eq(code).groupby(usagers["Num_Acc"]).sum()
    user_summary["n_gravite_inconnue"] = (~usagers["grav"].isin(GRAV_CODES)).groupby(usagers["Num_Acc"]).sum()
    if not (
        user_summary[[f"n_{s}" for s in GRAV_CODES.values()]].sum(axis=1)
        + user_summary["n_gravite_inconnue"]
    ).eq(user_summary["n_usagers"]).all():
        raise ValueError(f"{year} : gravités ne couvrent pas tous les usagers")

    # Le piéton BAAC est rattaché au véhicule heurtant, mais n'en est pas occupant.
    mode_users = linked.loc[linked["catu"].isin([1, 2])].copy()
    for mode, codes in MODES.items():
        selected = mode_users["catv"].isin(codes)
        for grav_code, grav_label in GRAV_CODES.items():
            user_summary[f"n_{mode}_{grav_label}"] = (
                (selected & mode_users["grav"].eq(grav_code))
                .groupby(mode_users["Num_Acc"]).sum()
            )
        user_summary[f"n_{mode}_gravite_inconnue"] = (
            (selected & ~mode_users["grav"].isin(GRAV_CODES))
            .groupby(mode_users["Num_Acc"]).sum()
        )
    user_summary = user_summary.fillna(0).reset_index()

    result = (
        matches.merge(caract, on="Num_Acc", how="left", validate="one_to_one")
        .merge(veh_summary, on="Num_Acc", how="left", validate="one_to_one")
        .merge(user_summary, on="Num_Acc", how="left", validate="one_to_one")
    )
    result = gpd.GeoDataFrame(result, geometry="geometry", crs=matches.crs)
    result = result.rename(columns={"an": "an_baac"})
    result["annee"] = year
    result["n_edpm"] = result["n_catv_50"]
    result["n_velos"] = result["n_catv_01"] + result["n_catv_80"]
    result["n_vl"] = result["n_catv_07"]
    result["n_tiers_vehicules_edpm"] = (
        result["n_vehicules"] - result["n_edpm"]
    ).where(result["n_edpm"].gt(0), 0)
    if len(result) != len(matches) or not result["Num_Acc"].is_unique:
        raise ValueError(f"{year} : table finale non unique par accident")
    if result["n_vehicules"].isna().any() or result["n_usagers"].isna().any():
        raise ValueError(f"{year} : accident sans résumé véhicules ou usagers")

    count_cols = [
        "n_vehicules", *CATV_COLUMNS, "n_catv_inconnu", "n_usagers", "n_pietons",
        *[f"n_{s}" for s in GRAV_CODES.values()], "n_gravite_inconnue",
        *[f"n_{mode}_{s}" for mode in MODES for s in (*GRAV_CODES.values(), "gravite_inconnue")],
        "n_edpm", "n_velos", "n_vl", "n_tiers_vehicules_edpm",
    ]
    result[count_cols] = result[count_cols].astype("int64")
    if not (result["n_velo_indemnes"].eq(result["n_velo_sans_assistance_indemnes"] + result["n_vae_indemnes"])).all():
        raise ValueError(f"{year} : incohérence des comptes vélo/VAE")
    for grav in (*GRAV_CODES.values(), "gravite_inconnue"):
        if not result[f"n_velo_{grav}"].eq(result[f"n_velo_sans_assistance_{grav}"] + result[f"n_vae_{grav}"]).all():
            raise ValueError(f"{year} : incohérence vélo/VAE pour {grav}")

    output = OUTPUT_ROOT / f"accidents-analyse-{year}.geoparquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Sortie déjà présente : {output}")
    result.to_parquet(output, index=False)
    print(f"  Sortie : {len(result):,} accidents ; {len(result.columns)} colonnes ; {int(result['n_edpm'].gt(0).sum()):,} accidents EDPM")
    print(f"  {output}")


if __name__ == "__main__":
    for year in YEARS:
        build_year(year)
