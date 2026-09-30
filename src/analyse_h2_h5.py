from __future__ import annotations

"""Diagnostic lecture OSM vs jointure par source et identifiant, 2019--2024.

uv run python src/analyse_h2_h5.py --start-year 2019 --end-year 2025
Lit outputs/tables/accidents-analyse-AAAA.geoparquet et
osm/derived/<AA>0101/*.geoparquet. Ne crée ni infractions ni causes.
Produit diagnostiques par accident et par source, et un résumé annuel.
N'écrase pas de sorties existantes.
"""

import argparse
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


def options():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start-year", type=int, default=2019)
    p.add_argument("--end-year", type=int, default=2025)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--output-dir", type=Path)
    return p.parse_args()


def normalise_id(series):
    s = series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    return s.mask(s.eq(""))


def voies(root, year):
    folder = root / "osm" / "derived" / f"{year % 100:02d}0101"
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    files = sorted(folder.glob("*.geoparquet"))
    if not files:
        raise FileNotFoundError(f"Aucun GeoParquet OSM dans {folder}")
    return {f.name: f for f in files}


def accident_table(root, year):
    path = root / "outputs" / "tables" / f"accidents-analyse-{year}.geoparquet"
    if not path.is_file():
        raise FileNotFoundError(path)
    required = {"Num_Acc", "osm_source", "osm_way_id", "highway", "match_status", "distance_m"}
    names = set(pq.read_schema(path).names)
    if required - names:
        raise ValueError(f"{path}: colonnes manquantes {sorted(required - names)}")
    a = pd.read_parquet(path, columns=sorted(required))
    a["Num_Acc"] = normalise_id(a["Num_Acc"])
    a["osm_way_id"] = normalise_id(a["osm_way_id"])
    a["osm_source_brut"] = a["osm_source"].astype("string")
    a["source_basename"] = a["osm_source_brut"].str.replace(r"^.*[/\\]", "", regex=True)
    a["annee"] = year
    return a


def report_year(root, year):
    files = voies(root, year)
    a = accident_table(root, year)
    a["fichier_source_existe"] = a["source_basename"].isin(files)
    a["source_file"] = a["source_basename"].where(a["fichier_source_existe"])
    a["voie_osm_retrouvee"] = False
    a["classe_osm"] = pd.NA
    a["raison"] = "voie_introuvable"
    a.loc[a["osm_way_id"].isna(), "raison"] = "osm_way_id_absent"
    a.loc[a["source_basename"].isna(), "raison"] = "osm_source_absente"
    a.loc[a["source_basename"].notna() & ~a["fichier_source_existe"], "raison"] = "nom_source_ne_correspond_pas"
    source_stats = []
    for source, rows in a.loc[a["fichier_source_existe"] & a["osm_way_id"].notna()].groupby("source_file"):
        f = files[source]
        names = set(pq.read_schema(f).names)
        if "osm_way_id" not in names:
            raise ValueError(f"{f}: osm_way_id absent")
        columns = ["osm_way_id"] + (["highway"] if "highway" in names else [])
        r = pd.read_parquet(f, columns=columns)
        r["osm_way_id"] = normalise_id(r["osm_way_id"])
        subset = r.loc[r["osm_way_id"].isin(rows["osm_way_id"])].drop_duplicates()
        if subset.duplicated("osm_way_id").any():
            raise ValueError(f"{f}: plusieurs voies distinctes pour osm_way_id")
        lookup = subset.set_index("osm_way_id")
        ix = rows.index
        a.loc[ix, "voie_osm_retrouvee"] = rows["osm_way_id"].isin(lookup.index).to_numpy()
        if "highway" in lookup:
            a.loc[ix, "classe_osm"] = rows["osm_way_id"].map(lookup["highway"]).to_numpy()
        a.loc[ix[a.loc[ix, "voie_osm_retrouvee"].to_numpy()], "raison"] = "retrouvee"
        source_stats.append({"annee": year, "osm_source": source,
                             "accidents": len(ix), "voies_retrouvees": int(a.loc[ix, "voie_osm_retrouvee"].sum()),
                             "fichier": str(f), "colonnes": ",".join(sorted(names))})
    a["classe_divergente"] = (a["voie_osm_retrouvee"]
                               & a["highway"].astype("string").ne(a["classe_osm"].astype("string"))).fillna(False)
    a["osm_source_brut_exemple"] = a["osm_source_brut"]
    return a, pd.DataFrame(source_stats)


def main():
    args = options()
    if args.start_year >= args.end_year:
        raise ValueError("Période invalide")
    root = args.root.resolve()
    out = args.output_dir or root / "outputs" / "h2_h5_diagnostic"
    suffix = f"{args.start_year}_{args.end_year}"
    paths = {name: out / f"{name}_{suffix}.csv" for name in
             ("diagnostic_accidents", "diagnostic_sources", "diagnostic_annuel")}
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("Sorties déjà présentes ; choisir --output-dir")
    yearly = [report_year(root, year) for year in range(args.start_year, args.end_year)]
    all_rows = pd.concat([item[0] for item in yearly], ignore_index=True)
    source_rows = pd.concat([item[1] for item in yearly], ignore_index=True)
    annual = all_rows.groupby(["annee", "raison"], dropna=False).size().rename("accidents").reset_index()
    out.mkdir(parents=True, exist_ok=True)
    all_rows.to_csv(paths["diagnostic_accidents"], index=False)
    source_rows.to_csv(paths["diagnostic_sources"], index=False)
    annual.to_csv(paths["diagnostic_annuel"], index=False)
    for path in paths.values():
        print(path)


if __name__ == "__main__":
    main()
