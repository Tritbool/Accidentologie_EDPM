from __future__ import annotations

"""H2/H5 : analyse BAAC--OSM millésimée avec contrôle des jointures.

uv run python src/analyse_h2_h5.py --start-year 2019 --end-year 2025
Lit outputs/tables/accidents-analyse-AAAA.geoparquet,
BAAC/derived/AAAA/vehicules-AAAA.parquet et
osm/derived/<AA>0101/*.geoparquet. Une exécution crée : cas au grain
accident x mode, synthèse par voie/zone/mode, contrôle annuel des jointures
et cas à vérifier. Ne réécrit pas des sorties existantes.
Aucun tag OSM ne démontre à lui seul la trajectoire ni une infraction.
"""

import argparse
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

MODES = {1: "VELO", 80: "VAE", 50: "EDPM", 2: "CYCLO"}
ZONES = {1: "Hors agglomération", 2: "En agglomération"}
NON_MOTORISES = {1, 50, 60, 80}
INDETERMINES = {0, 99}
TAGS = ("highway", "access", "bicycle", "motor_vehicle", "surface", "smoothness",
        "cycleway", "cycleway:left", "cycleway:right", "maxspeed", "oneway", "name")
TAG_COLS = [f"osm_{x.replace(':', '_')}" for x in TAGS]


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start-year", type=int, default=2019)
    p.add_argument("--end-year", type=int, default=2025)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--output-dir", type=Path)
    p.add_argument("--max-distance-m", type=float, default=30.0)
    return p.parse_args()


def norm_ids(series, allow_null=False):
    x = series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    x = x.mask(x.eq(""))
    if not allow_null and x.isna().any():
        raise ValueError("Identifiant manquant")
    return x


def read_accidents(path, year):
    if not path.is_file():
        raise FileNotFoundError(path)
    names = set(pq.read_schema(path).names)
    required = {"Num_Acc", "agg", "highway", "osm_way_id", "osm_source", "distance_m",
                "match_status", "n_pietons", "n_vehicules",
                *(f"n_catv_{code:02d}" for code in MODES)}
    optional = {"int", "col"}
    if required - names:
        raise ValueError(f"{path} : colonnes absentes {sorted(required - names)}")
    a = pd.read_parquet(path, columns=sorted(required | (names & optional)))
    for column in optional - names:
        a[column] = pd.NA
    a["Num_Acc"] = norm_ids(a["Num_Acc"])
    if a["Num_Acc"].duplicated().any():
        raise ValueError(f"{path} : Num_Acc dupliqué")
    a["osm_way_id"] = norm_ids(a["osm_way_id"], allow_null=True)
    a["osm_source"] = a["osm_source"].astype("string")
    a["annee"] = year
    a["zone"] = pd.to_numeric(a["agg"], errors="coerce").map(ZONES).fillna("Inconnue")
    a["distance_m"] = pd.to_numeric(a["distance_m"], errors="coerce")
    for column in ["n_pietons", "n_vehicules", *(f"n_catv_{code:02d}" for code in MODES)]:
        a[column] = pd.to_numeric(a[column], errors="raise")
        if a[column].isna().any() or a[column].lt(0).any() or a[column].ne(a[column].round()).any():
            raise ValueError(f"{path} : compteur invalide {column}")
    a = a.loc[a[[f"n_catv_{code:02d}" for code in MODES]].gt(0).any(axis=1)].copy()
    return a


def read_vehicles(path, ids):
    if not path.is_file():
        raise FileNotFoundError(path)
    required = {"Num_Acc", "id_vehicule", "catv"}
    if required - set(pq.read_schema(path).names):
        raise ValueError(f"{path} : colonnes véhicules manquantes")
    v = pd.read_parquet(path, columns=sorted(required))
    v["Num_Acc"] = norm_ids(v["Num_Acc"])
    v = v.loc[v["Num_Acc"].isin(ids)].copy()
    v["id_vehicule"] = norm_ids(v["id_vehicule"])
    if v.duplicated(["Num_Acc", "id_vehicule"]).any():
        raise ValueError(f"{path} : véhicules dupliqués")
    v["catv"] = pd.to_numeric(v["catv"], errors="coerce").astype("Int64")
    return v


def snapshot_files(root, year):
    directory = root / "osm" / "derived" / f"{year % 100:02d}0101"
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    files = sorted(directory.glob("*.geoparquet"))
    if not files:
        raise FileNotFoundError(f"Aucun GeoParquet OSM dans {directory}")
    return {path.name: path for path in files}


def source_basename(s):
    return s.astype("string").str.replace(r"^.*[/\\]", "", regex=True)


def read_roads(files, accidents):
    pairs = accidents.loc[accidents["osm_way_id"].notna(),
                          ["source_file", "osm_way_id"]].dropna().drop_duplicates()
    unknown_sources = sorted(set(pairs["source_file"]) - set(files))
    if unknown_sources:
        raise ValueError(f"Sources OSM absentes du millésime : {unknown_sources[:12]}")
    chunks = []
    for name, required in pairs.groupby("source_file", sort=False):
        path = files[name]
        names = set(pq.read_schema(path).names)
        if "osm_way_id" not in names:
            raise ValueError(f"{path} : osm_way_id manquant")
        available = [tag for tag in TAGS if tag in names]
        roads = pd.read_parquet(path, columns=["osm_way_id", *available])
        roads["osm_way_id"] = norm_ids(roads["osm_way_id"])
        roads = roads.loc[roads["osm_way_id"].isin(required["osm_way_id"])].copy()
        if roads.empty:
            continue
        roads["source_file"] = name
        for tag in TAGS:
            roads[f"osm_{tag.replace(':', '_')}"] = (
                roads[tag].astype("string") if tag in roads else pd.Series(pd.NA, index=roads.index, dtype="string"))
        roads = roads[["source_file", "osm_way_id", *TAG_COLS]].drop_duplicates()
        if roads.duplicated(["source_file", "osm_way_id"]).any():
            raise ValueError(f"{path} : tags divergents pour un même osm_way_id")
        chunks.append(roads)
    if chunks:
        return pd.concat(chunks, ignore_index=True)
    return pd.DataFrame(columns=["source_file", "osm_way_id", *TAG_COLS])


def vehicle_counts(v, a):
    codes = pd.crosstab(v["Num_Acc"], v["catv"])
    codes.columns = [f"n_code_{int(code):02d}" for code in codes.columns]
    codes["n_code_inconnu"] = v["catv"].isna().groupby(v["Num_Acc"]).sum()
    if set(a["Num_Acc"]) - set(codes.index):
        raise ValueError("Accidents sans véhicules dans le BAAC dérivé")
    counts = codes.reindex(a["Num_Acc"])
    if not counts.sum(axis=1).to_numpy().astype(int).tolist() == a["n_vehicules"].astype(int).tolist():
        raise ValueError("Incohérence n_vehicules entre BAAC et table accidents")
    return codes


def enrich_year(a, v, files, max_distance):
    a = a.copy()
    a["source_file"] = source_basename(a["osm_source"])
    roads = read_roads(files, a)
    a = a.merge(roads, how="left", on=["source_file", "osm_way_id"],
                validate="many_to_one", indicator="_jointure")
    a["osm_jointure"] = "retrouvee"
    a.loc[a["osm_way_id"].isna(), "osm_jointure"] = "osm_way_id_absent"
    a.loc[a["osm_way_id"].notna() & ~a["source_file"].isin(files), "osm_jointure"] = "source_introuvable"
    a.loc[a["osm_way_id"].notna() & a["source_file"].isin(files)
          & a["_jointure"].ne("both"), "osm_jointure"] = "way_introuvable"
    if a.loc[a["osm_way_id"].notna(), "osm_jointure"].ne("retrouvee").any():
        bad = a.loc[a["osm_way_id"].notna() & a["osm_jointure"].ne("retrouvee"),
                    ["annee", "Num_Acc", "source_file", "osm_way_id", "osm_jointure"]]
        raise ValueError(f"Jointure incomplète pour des accidents matchés ; exemples : {bad.head(5).to_dict('records')}")
    a["classe_osm_divergente"] = (a["osm_jointure"].eq("retrouvee")
                                  & a["highway"].astype("string").ne(a["osm_highway"].astype("string"))).fillna(False)
    counts = vehicle_counts(v, a)
    a = a.join(counts, on="Num_Acc")
    motor_cols = [c for c in counts if c.startswith("n_code_") and c[7:].isdigit()
                  and int(c[7:]) not in NON_MOTORISES | INDETERMINES]
    a["n_motorises_total"] = a[motor_cols].sum(axis=1).astype(int) if motor_cols else 0
    a["n_vl"] = a["n_code_07"].astype(int) if "n_code_07" in a else 0
    a["n_cyclos"] = a["n_code_02"].astype(int) if "n_code_02" in a else 0
    a["distance_a_verifier"] = a["distance_m"].isna() | a["distance_m"].gt(max_distance)
    a["intersection_baac_renseignee"] = pd.to_numeric(a["int"], errors="coerce").isin(range(1, 10))
    a["intersection_baac"] = a["intersection_baac_renseignee"] & pd.to_numeric(a["int"], errors="coerce").ne(1)
    out = []
    for code, mode in MODES.items():
        f = a.loc[a[f"n_catv_{code:02d}"].gt(0)].copy()
        if f.empty:
            continue
        f["mode"] = mode
        f["n_autres_motorises"] = f["n_motorises_total"] - (f["n_cyclos"] if code == 2 else 0)
        f["tiers_motorise"] = f["n_autres_motorises"].gt(0)
        f["tiers_vl"] = f["n_vl"].gt(0)
        f["bande_ou_piste_tag_osm"] = f[["osm_cycleway", "osm_cycleway_left", "osm_cycleway_right"]].notna().any(axis=1)
        f["cible_h2"] = (f["highway"].eq("cycleway") & f["mode"].isin(["VELO", "VAE", "EDPM"])
                         & f["tiers_motorise"])
        f["cible_h5_edpm"] = (f["zone"].eq("Hors agglomération") & f["mode"].eq("EDPM")
                              & f["highway"].notna() & f["highway"].ne("cycleway"))
        f["cible_h5_cyclo"] = f["highway"].eq("cycleway") & f["mode"].eq("CYCLO")
        f["acces_a_verifier"] = "aucune_conclusion_juridique_automatique"
        f.loc[f["cible_h5_edpm"], "acces_a_verifier"] = "route_hors_agglomeration_regle_locale_a_verifier"
        f.loc[f["highway"].eq("cycleway"), "acces_a_verifier"] = "voie_cyclable_trajectoires_et_acces_a_verifier"
        f["controle_a_verifier"] = (f["osm_jointure"].ne("retrouvee") | f["classe_osm_divergente"]
                                    | f["distance_a_verifier"])
        keep = ["annee", "Num_Acc", "mode", "zone", "highway", "osm_way_id", "osm_source",
                "source_file", "osm_jointure", "classe_osm_divergente", "distance_m", "distance_a_verifier",
                "match_status", "n_vehicules", "n_pietons", "n_vl", "n_cyclos", "n_autres_motorises",
                "tiers_motorise", "tiers_vl", "int", "col", "intersection_baac_renseignee",
                "intersection_baac", "bande_ou_piste_tag_osm", "acces_a_verifier",
                "cible_h2", "cible_h5_edpm", "cible_h5_cyclo", "controle_a_verifier", *TAG_COLS]
        out.append(f[keep])
    if not out:
        raise ValueError("Aucun accident des modes retenus")
    return pd.concat(out, ignore_index=True)


def make_summaries(cases):
    cols = ["annee", "zone", "highway", "mode"]
    result = cases.groupby(cols, dropna=False).agg(
        accidents=("Num_Acc", "size"), avec_tiers_motorise=("tiers_motorise", "sum"),
        avec_vl=("tiers_vl", "sum"), h2_a_examiner=("cible_h2", "sum"),
        h5_edpm_a_examiner=("cible_h5_edpm", "sum"), h5_cyclo_a_examiner=("cible_h5_cyclo", "sum"),
        voies_osm_retrouvees=("osm_jointure", lambda x: int(x.eq("retrouvee").sum())),
        classe_osm_divergente=("classe_osm_divergente", "sum"),
        intersection_baac=("intersection_baac", "sum"),
    ).reset_index()
    for col in ("avec_tiers_motorise", "avec_vl", "h2_a_examiner", "h5_edpm_a_examiner",
                "h5_cyclo_a_examiner", "intersection_baac"):
        result[f"part_{col}_pct"] = (100 * result[col] / result["accidents"]).round(2)
    return result


def checks_year(cases):
    one = cases.drop_duplicates(["annee", "Num_Acc"])
    return one.groupby(["annee", "osm_jointure"], dropna=False).agg(
        accidents=("Num_Acc", "size"), classes_divergentes=("classe_osm_divergente", "sum"),
        distances_a_verifier=("distance_a_verifier", "sum")
    ).reset_index()


def main():
    opts = arguments()
    if opts.start_year >= opts.end_year or opts.max_distance_m <= 0:
        raise ValueError("Période ou --max-distance-m invalide")
    root = opts.root.resolve()
    out = opts.output_dir or root / "outputs" / "h2_h5"
    suffix = f"{opts.start_year}_{opts.end_year}"
    paths = {name: out / f"{name}_{suffix}.csv" for name in
             ("cas_enrichis_h2_h5", "synthese_h2_h5", "controle_annuel_h2_h5", "verifications_h2_h5")}
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing:
        raise FileExistsError("Sorties déjà présentes ; choisir --output-dir : " + ", ".join(existing))
    chunks = []
    for year in range(opts.start_year, opts.end_year):
        a = read_accidents(root / "outputs" / "tables" / f"accidents-analyse-{year}.geoparquet", year)
        if a.empty:
            continue
        files = snapshot_files(root, year)
        v = read_vehicles(root / "BAAC" / "derived" / str(year) / f"vehicules-{year}.parquet",
                          set(a["Num_Acc"]))
        chunks.append(enrich_year(a, v, files, opts.max_distance_m))
    if not chunks:
        raise ValueError("Aucun accident des modes sélectionnés")
    cases = pd.concat(chunks, ignore_index=True)
    summary = make_summaries(cases)
    annual = checks_year(cases)
    verifications = cases.loc[
        cases[["cible_h2", "cible_h5_edpm", "cible_h5_cyclo", "controle_a_verifier"]].any(axis=1)
    ].copy()
    out.mkdir(parents=True, exist_ok=True)
    cases.to_csv(paths["cas_enrichis_h2_h5"], index=False)
    summary.to_csv(paths["synthese_h2_h5"], index=False)
    annual.to_csv(paths["controle_annuel_h2_h5"], index=False)
    verifications.to_csv(paths["verifications_h2_h5"], index=False)
    for path in paths.values():
        print(path)


if __name__ == "__main__":
    main()
