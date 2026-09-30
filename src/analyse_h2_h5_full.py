from __future__ import annotations

"""H2/H5 : cas BAAC, tags et voisinage géométrique OSM millésimé.

uv run python src/analyse_h2_h5.py --start-year 2019 --end-year 2025 \\
    --output-dir outputs/h2_h5_geometrie
Conserve cas/synthèse/couverture tags/contrôle/vérifications et ajoute un
contexte des voies croisées/proches pour les seuls cas H2/H5. Un croisement
géométrique est un indice, jamais une trajectoire, faute ou infraction.
"""

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq

MODES = {1: "VELO", 80: "VAE", 50: "EDPM", 2: "CYCLO"}
ZONES = {1: "Hors agglomération", 2: "En agglomération"}
NON_MOTORISES = {1, 50, 60, 80}
INDETERMINES = {0, 99}
TAGS = ("highway", "access", "bicycle", "motor_vehicle", "surface", "smoothness",
        "cycleway", "cycleway:left", "cycleway:right", "maxspeed", "oneway", "name")
TAG_COLS = [f"osm_{x.replace(':', '_')}" for x in TAGS]
ROAD_MOTORIZED = {"motorway", "trunk", "primary", "secondary", "tertiary",
                  "unclassified", "residential", "living_street", "service"}


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start-year", type=int, default=2019)
    p.add_argument("--end-year", type=int, default=2025)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--output-dir", type=Path)
    p.add_argument("--max-distance-m", type=float, default=30.)
    p.add_argument("--nearby-m", type=float, default=30.,
                   help="Rayon autour du point BAAC pour les voies voisines")
    p.add_argument("--crossing-m", type=float, default=10.,
                   help="Distance point BAAC--croisement géométrique")
    p.add_argument("--crs-metric", default="EPSG:2154",
                   help="CRS métrique adapté au territoire ; défaut : métropole")
    return p.parse_args()


def norm_id(series, allow_null=False):
    x = series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    x = x.mask(x.eq(""))
    if not allow_null and x.isna().any():
        raise ValueError("Identifiant manquant")
    return x


def read_accidents(path, year):
    if not path.is_file():
        raise FileNotFoundError(path)
    cols = set(pq.read_schema(path).names)
    required = {"Num_Acc", "agg", "highway", "osm_way_id", "osm_source", "distance_m",
                "match_status", "n_pietons", "n_vehicules", "geometry",
                *(f"n_catv_{code:02d}" for code in MODES)}
    if required - cols:
        raise ValueError(f"{path}: colonnes absentes {sorted(required - cols)}")
    a = gpd.read_parquet(path, columns=sorted(required | (cols & {"int", "col"})))
    if a.crs is None:
        raise ValueError(f"{path}: CRS manquant")
    for c in {"int", "col"} - cols:
        a[c] = pd.NA
    a["Num_Acc"] = norm_id(a["Num_Acc"])
    a["osm_way_id"] = norm_id(a["osm_way_id"], True)
    a["osm_source"] = a["osm_source"].astype("string")
    if a["Num_Acc"].duplicated().any():
        raise ValueError(f"{path}: Num_Acc non unique")
    a["annee"] = year
    a["zone"] = pd.to_numeric(a["agg"], errors="coerce").map(ZONES).fillna("Inconnue")
    a["distance_m"] = pd.to_numeric(a["distance_m"], errors="coerce")
    for c in ["n_pietons", "n_vehicules", *(f"n_catv_{code:02d}" for code in MODES)]:
        a[c] = pd.to_numeric(a[c], errors="raise")
        if a[c].isna().any() or a[c].lt(0).any() or a[c].ne(a[c].round()).any():
            raise ValueError(f"{path}: compteur invalide {c}")
    return a.loc[a[[f"n_catv_{code:02d}" for code in MODES]].gt(0).any(axis=1)].copy()


def read_vehicles(path, ids):
    if not path.is_file():
        raise FileNotFoundError(path)
    cols = {"Num_Acc", "id_vehicule", "catv"}
    if cols - set(pq.read_schema(path).names):
        raise ValueError(f"{path}: colonnes véhicules absentes")
    v = pd.read_parquet(path, columns=sorted(cols))
    v["Num_Acc"] = norm_id(v["Num_Acc"])
    v = v.loc[v["Num_Acc"].isin(ids)].copy()
    v["id_vehicule"] = norm_id(v["id_vehicule"])
    if v.duplicated(["Num_Acc", "id_vehicule"]).any():
        raise ValueError(f"{path}: véhicule dupliqué")
    v["catv"] = pd.to_numeric(v["catv"], errors="coerce").astype("Int64")
    return v


def snapshot_files(root, year):
    folder = root / "osm" / "derived" / f"{year % 100:02d}0101"
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    files = sorted(folder.glob("*.geoparquet"))
    if not files:
        raise FileNotFoundError(f"Aucun GeoParquet OSM dans {folder}")
    return {p.name: p for p in files}


def read_roads(files, a):
    pairs = a.loc[a["osm_way_id"].notna(), ["source_file", "osm_way_id"]].dropna().drop_duplicates()
    missing = sorted(set(pairs["source_file"]) - set(files))
    if missing:
        raise ValueError(f"Sources OSM introuvables: {missing[:10]}")
    chunks, availability = [], []
    for source, need in pairs.groupby("source_file", sort=False):
        file = files[source]
        names = set(pq.read_schema(file).names)
        if "osm_way_id" not in names:
            raise ValueError(f"{file}: osm_way_id absent")
        availability.append({"annee": int(a["annee"].iloc[0]), "source_file": source,
                             **{f"champ_{tag.replace(':', '_')}_present": tag in names for tag in TAGS}})
        fields = [tag for tag in TAGS if tag in names]
        r = pd.read_parquet(file, columns=["osm_way_id", *fields])
        r["osm_way_id"] = norm_id(r["osm_way_id"])
        r = r.loc[r["osm_way_id"].isin(need["osm_way_id"])].copy()
        if r.empty:
            continue
        r["source_file"] = source
        for tag in TAGS:
            r[f"osm_{tag.replace(':', '_')}"] = r[tag].astype("string") if tag in r else pd.Series(pd.NA, index=r.index, dtype="string")
        r = r[["source_file", "osm_way_id", *TAG_COLS]].drop_duplicates()
        if r.duplicated(["source_file", "osm_way_id"]).any():
            raise ValueError(f"{file}: tags divergents pour un même osm_way_id")
        chunks.append(r)
    roads = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=["source_file", "osm_way_id", *TAG_COLS])
    return roads, pd.DataFrame(availability)


def vehicle_counts(v, a):
    counts = pd.crosstab(v["Num_Acc"], v["catv"])
    counts.columns = [f"n_code_{int(c):02d}" for c in counts.columns]
    counts["n_code_inconnu"] = v["catv"].isna().groupby(v["Num_Acc"]).sum()
    if set(a["Num_Acc"]) - set(counts.index):
        raise ValueError("Accidents sans véhicule retrouvé")
    if not (counts.sum(axis=1).reindex(a["Num_Acc"]).to_numpy() == a["n_vehicules"].to_numpy()).all():
        raise ValueError("Incohérence n_vehicules")
    return counts


def enrich(a, v, files, max_distance):
    a = a.copy()
    a["source_file"] = a["osm_source"].str.replace(r"^.*[/\\]", "", regex=True)
    roads, availability = read_roads(files, a)
    a = a.merge(roads, on=["source_file", "osm_way_id"], how="left", validate="many_to_one", indicator="_join")
    a["osm_jointure"] = "retrouvee"
    a.loc[a["osm_way_id"].isna(), "osm_jointure"] = "osm_way_id_absent"
    a.loc[a["osm_way_id"].notna() & a["_join"].ne("both"), "osm_jointure"] = "way_introuvable"
    bad = a.loc[a["osm_way_id"].notna() & a["osm_jointure"].ne("retrouvee")]
    if not bad.empty:
        raise ValueError("Jointure OSM incomplète: " + repr(bad[["annee", "Num_Acc", "source_file", "osm_way_id"]].head().to_dict("records")))
    a["classe_osm_divergente"] = (a["osm_jointure"].eq("retrouvee") & a["highway"].astype("string").ne(a["osm_highway"].astype("string"))).fillna(False)
    counts = vehicle_counts(v, a)
    a = a.join(counts, on="Num_Acc")
    motor_cols = [c for c in counts if c.startswith("n_code_") and c[7:].isdigit()
                  and int(c[7:]) not in NON_MOTORISES | INDETERMINES]
    a["n_motorises_total"] = a[motor_cols].sum(axis=1).astype(int) if motor_cols else 0
    a["n_vl"] = a["n_code_07"].astype(int) if "n_code_07" in a else 0
    a["n_cyclos"] = a["n_code_02"].astype(int) if "n_code_02" in a else 0
    a["distance_a_verifier"] = a["distance_m"].isna() | a["distance_m"].gt(max_distance)
    baac_int = pd.to_numeric(a["int"], errors="coerce")
    a["intersection_baac_renseignee"] = baac_int.isin(range(1, 10))
    a["intersection_baac"] = a["intersection_baac_renseignee"] & baac_int.ne(1)
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
        f["cible_h2"] = f["highway"].eq("cycleway") & f["mode"].isin(["VELO", "VAE", "EDPM"]) & f["tiers_motorise"]
        f["cible_h5_edpm"] = f["mode"].eq("EDPM") & f["zone"].eq("Hors agglomération") & f["highway"].notna() & f["highway"].ne("cycleway")
        f["cible_h5_cyclo"] = f["highway"].eq("cycleway") & f["mode"].eq("CYCLO")
        f["acces_a_verifier"] = "pas_de_conclusion_juridique_automatique"
        f.loc[f["cible_h5_edpm"], "acces_a_verifier"] = "route_hors_agglo_autorisation_locale_a_verifier"
        f.loc[f["highway"].eq("cycleway"), "acces_a_verifier"] = "cycleway_trajectoires_et_acces_a_verifier"
        f["controle_a_verifier"] = f["osm_jointure"].ne("retrouvee") | f["classe_osm_divergente"] | f["distance_a_verifier"]
        out.append(f[["annee", "Num_Acc", "mode", "zone", "highway", "osm_way_id", "osm_source", "source_file",
                      "osm_jointure", "classe_osm_divergente", "distance_m", "distance_a_verifier", "match_status",
                      "n_vehicules", "n_pietons", "n_vl", "n_cyclos", "n_autres_motorises", "tiers_motorise",
                      "tiers_vl", "int", "col", "intersection_baac_renseignee", "intersection_baac",
                      "bande_ou_piste_tag_osm", "acces_a_verifier", "cible_h2", "cible_h5_edpm",
                      "cible_h5_cyclo", "controle_a_verifier", *TAG_COLS]])
    return pd.concat(out, ignore_index=True), availability


def spatial_context(cases, accident_points, files, nearby_m, crossing_m, crs_metric):
    keys = ["annee", "Num_Acc"]
    focus = cases.loc[cases[["cible_h2", "cible_h5_edpm", "cible_h5_cyclo"]].any(axis=1),
                      keys + ["source_file", "osm_way_id", "osm_jointure"]].drop_duplicates(keys)
    if focus.empty:
        return pd.DataFrame(columns=keys + ["contexte_statut", "nb_voies_voisines", "nb_routes_motorisees_proches",
                                           "nb_routes_croisantes", "croisement_proche_point", "distance_croisement_m"])
    points = accident_points[["Num_Acc", "geometry"]].copy()
    point_crs = points.crs
    if point_crs is None:
        raise ValueError("Points BAAC sans CRS")
    focus = focus.merge(points, on="Num_Acc", how="left", validate="one_to_one")
    focus = gpd.GeoDataFrame(focus, geometry="geometry", crs=point_crs)
    if focus.geometry.isna().any() or focus.geometry.is_empty.any():
        raise ValueError("Cas ciblés avec géométrie BAAC absente")
    focus = focus.to_crs(crs_metric)
    results = []
    for source, items in focus.groupby("source_file", dropna=False):
        if pd.isna(source) or source not in files:
            for row in items.itertuples():
                results.append({"annee": row.annee, "Num_Acc": row.Num_Acc, "contexte_statut": "source_absente"})
            continue
        path = files[source]
        names = set(pq.read_schema(path).names)
        columns = ["osm_way_id", "geometry", *(tag for tag in ("highway", "motor_vehicle", "access") if tag in names)]
        if "geometry" not in names:
            raise ValueError(f"{path}: géométrie manquante")
        roads = gpd.read_parquet(path, columns=columns)
        if roads.crs is None:
            raise ValueError(f"{path}: CRS manquant")
        roads = roads.loc[roads.geometry.notna() & ~roads.geometry.is_empty].to_crs(crs_metric)
        roads["osm_way_id"] = norm_id(roads["osm_way_id"])
        roads = roads.reset_index(drop=True)
        spatial = roads.sindex
        for row in items.itertuples():
            rec = {"annee": row.annee, "Num_Acc": row.Num_Acc, "contexte_statut": "evalue",
                   "nb_voies_voisines": 0, "nb_routes_motorisees_proches": 0,
                   "nb_routes_croisantes": 0, "croisement_proche_point": False,
                   "distance_croisement_m": pd.NA}
            nearby = list(spatial.query(row.geometry.buffer(nearby_m), predicate="intersects"))
            nearby = roads.iloc[nearby].copy()
            nearby = nearby.loc[nearby.geometry.distance(row.geometry).le(nearby_m)]
            nearby = nearby.loc[nearby["osm_way_id"].ne(row.osm_way_id)]
            rec["nb_voies_voisines"] = len(nearby)
            if "highway" in nearby:
                motor = nearby.loc[nearby["highway"].isin(ROAD_MOTORIZED)]
                rec["nb_routes_motorisees_proches"] = len(motor)
            else:
                motor = nearby.iloc[0:0]
            if row.osm_jointure != "retrouvee":
                rec["contexte_statut"] = "voie_retenue_introuvable"
            else:
                target = roads.loc[roads["osm_way_id"].eq(row.osm_way_id)]
                if target.empty:
                    rec["contexte_statut"] = "voie_retenue_absente_geometrie"
                else:
                    target_geom = target.geometry.union_all()
                    intersects = motor.loc[motor.geometry.intersects(target_geom)]
                    rec["nb_routes_croisantes"] = len(intersects)
                    distances = [row.geometry.distance(target_geom.intersection(g))
                                 for g in intersects.geometry]
                    if distances:
                        rec["distance_croisement_m"] = min(distances)
                        rec["croisement_proche_point"] = min(distances) <= crossing_m
            results.append(rec)
    return pd.DataFrame(results)


def synthese(cases):
    c = cases.copy()
    c["h2_intersection"] = c["cible_h2"] & c["intersection_baac"]
    c["h2_hors_intersection"] = c["cible_h2"] & c["intersection_baac_renseignee"] & ~c["intersection_baac"]
    c["h2_intersection_inconnue"] = c["cible_h2"] & ~c["intersection_baac_renseignee"]
    c["h2_vl"] = c["cible_h2"] & c["tiers_vl"]
    c["h2_vl_intersection"] = c["h2_vl"] & c["intersection_baac"]
    c["h2_vl_hors_intersection"] = c["h2_vl"] & c["intersection_baac_renseignee"] & ~c["intersection_baac"]
    c["h2_vl_intersection_inconnue"] = c["h2_vl"] & ~c["intersection_baac_renseignee"]
    c["h2_bande_ou_piste_tag_osm"] = c["cible_h2"] & c["bande_ou_piste_tag_osm"]
    c["h2_croisement_osm_proche"] = c["cible_h2"] & c["croisement_proche_point"].fillna(False)
    c["h2_voies_motorisees_voisines"] = c["cible_h2"] & c["nb_routes_motorisees_proches"].fillna(0).gt(0)
    c["h2_contexte_non_evalue"] = c["cible_h2"] & c["contexte_statut"].ne("evalue")
    groups = ["annee", "zone", "highway", "mode"]
    fields = ["tiers_motorise", "tiers_vl", "cible_h2", "cible_h5_edpm", "cible_h5_cyclo",
              "intersection_baac", "intersection_baac_renseignee", "h2_intersection",
              "h2_hors_intersection", "h2_intersection_inconnue", "h2_vl", "h2_vl_intersection",
              "h2_vl_hors_intersection", "h2_vl_intersection_inconnue", "h2_bande_ou_piste_tag_osm",
              "h2_croisement_osm_proche", "h2_voies_motorisees_voisines", "h2_contexte_non_evalue"]
    summary = c.groupby(groups, dropna=False).agg(
        accidents=("Num_Acc", "size"), voies_osm_retrouvees=("osm_jointure", lambda x: int(x.eq("retrouvee").sum())),
        classe_osm_divergente=("classe_osm_divergente", "sum"),
        **{field: (field, "sum") for field in fields}).reset_index()
    if not (summary["h2_intersection"] + summary["h2_hors_intersection"] + summary["h2_intersection_inconnue"]).eq(summary["cible_h2"]).all():
        raise ValueError("Partition intersection H2 invalide")
    if not (summary["h2_vl_intersection"] + summary["h2_vl_hors_intersection"] + summary["h2_vl_intersection_inconnue"]).eq(summary["h2_vl"]).all():
        raise ValueError("Partition intersection VL invalide")
    for measure, base in [("h2_intersection", "h2_intersection"), ("h2_vl_intersection", "h2_vl_intersection")]:
        off = "h2_hors_intersection" if measure == "h2_intersection" else "h2_vl_hors_intersection"
        summary[f"part_{base}_parmi_renseignes_pct"] = (100 * summary[measure] / (summary[measure] + summary[off]).replace(0, float("nan"))).round(2)
    return summary


def couverture_tags(cases, availability):
    c = cases.loc[cases[["cible_h2", "cible_h5_edpm", "cible_h5_cyclo"]].any(axis=1)].copy()
    if c.empty:
        return pd.DataFrame(columns=["annee", "zone", "highway", "mode", "tag", "cas_cibles", "fichiers_avec_champ", "valeurs_non_nulles", "valeurs_manquantes", "exemples_valeurs"])
    present = availability.set_index(["annee", "source_file"])
    out = []
    for key, group in c.groupby(["annee", "zone", "highway", "mode"], dropna=False):
        for tag in TAGS:
            col = f"osm_{tag.replace(':', '_')}"
            field = f"champ_{tag.replace(':', '_')}_present"
            file_keys = group[["annee", "source_file"]].drop_duplicates()
            count_files = sum(bool(present.loc[(int(y), src), field])
                              for y, src in file_keys.itertuples(index=False, name=None)
                              if pd.notna(src) and (int(y), src) in present.index)
            non_null = int(group[col].notna().sum())
            out.append({"annee": key[0], "zone": key[1], "highway": key[2], "mode": key[3],
                        "tag": tag, "cas_cibles": len(group), "fichiers_avec_champ": count_files,
                        "valeurs_non_nulles": non_null, "valeurs_manquantes": len(group)-non_null,
                        "exemples_valeurs": " | ".join(group[col].dropna().astype(str).value_counts().head(5).index)})
    return pd.DataFrame(out)


def main():
    args = arguments()
    if args.start_year >= args.end_year or min(args.max_distance_m, args.nearby_m, args.crossing_m) <= 0:
        raise ValueError("Période ou distance invalide")
    root = args.root.resolve()
    out = args.output_dir or root / "outputs" / "h2_h5"
    suffix = f"{args.start_year}_{args.end_year}"
    names = ("cas_enrichis_h2_h5", "synthese_h2_h5", "couverture_tags_h2_h5",
             "controle_annuel_h2_h5", "verifications_h2_h5")
    paths = {n: out / f"{n}_{suffix}.csv" for n in names}
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing:
        raise FileExistsError("Sorties présentes ; choisir --output-dir : " + ", ".join(existing))
    chunks, avail = [], []
    for year in range(args.start_year, args.end_year):
        a = read_accidents(root / "outputs" / "tables" / f"accidents-analyse-{year}.geoparquet", year)
        if a.empty:
            continue
        files = snapshot_files(root, year)
        v = read_vehicles(root / "BAAC" / "derived" / str(year) / f"vehicules-{year}.parquet", set(a["Num_Acc"]))
        rows, present = enrich(a, v, files, args.max_distance_m)
        spatial = spatial_context(rows, a, files, args.nearby_m, args.crossing_m, args.crs_metric)
        rows = rows.merge(spatial, on=["annee", "Num_Acc"], how="left", validate="many_to_one")
        rows["croisement_proche_point"] = rows["croisement_proche_point"].fillna(False).astype(bool)
        chunks.append(rows)
        avail.append(present)
    if not chunks:
        raise ValueError("Aucun accident des modes sélectionnés")
    cases = pd.concat(chunks, ignore_index=True)
    availability = pd.concat(avail, ignore_index=True)
    summary = synthese(cases)
    tags = couverture_tags(cases, availability)
    unique = cases.drop_duplicates(["annee", "Num_Acc"])
    annual = unique.groupby(["annee", "osm_jointure"], dropna=False).agg(
        accidents=("Num_Acc", "size"), classes_divergentes=("classe_osm_divergente", "sum"),
        distances_a_verifier=("distance_a_verifier", "sum")).reset_index()
    focus = cases.loc[cases[["cible_h2", "cible_h5_edpm", "cible_h5_cyclo", "controle_a_verifier"]].any(axis=1)]
    out.mkdir(parents=True, exist_ok=True)
    for name, df in [(names[0], cases), (names[1], summary), (names[2], tags),
                     (names[3], annual), (names[4], focus)]:
        df.to_csv(paths[name], index=False)
        print(paths[name])


if __name__ == "__main__":
    main()
