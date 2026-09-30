from __future__ import annotations

"""Bilan agrégé H2/H5 des tags d'accès de la voie OSM appariée.

Usage : python analyse_tags_h2_h5.py cas_enrichis_h2_h5_2019_2025.csv --output-dir resultats_tags
Ne publie pas d'identifiants, de noms de voie ni de lignes individuelles.
Les libellés d'accès sont DESCRIPTIFS, pas un jugement de légalité.
"""

import argparse
from pathlib import Path
import pandas as pd

GROUPS = ["zone", "mode", "population", "baac_intersection", "osm_croisement"]
TAGS = ["osm_access", "osm_motor_vehicle", "osm_bicycle", "osm_cycleway",
        "osm_cycleway_left", "osm_cycleway_right"]
REQUIRED = ["annee", "Num_Acc", "mode", "zone", "highway", "cible_h2", "cible_h5_edpm",
            "tiers_vl", "intersection_baac_renseignee", "intersection_baac",
            "contexte_statut", "croisement_proche_point", "osm_jointure", "distance_m",
            "classe_osm_divergente", *TAGS]
BASE = ["zone", "mode", "population"]


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", type=Path, help="CSV cas_enrichis_h2_h5...")
    p.add_argument("--output-dir", type=Path, default=Path("resultats_tags"))
    p.add_argument("--start-year", type=int, default=2019)
    p.add_argument("--end-year", type=int, default=2024, help="Inclus")
    return p.parse_args()


def boolean(s, name):
    v = s.astype("string").str.strip().str.lower()
    mapping = {"true": True, "false": False, "1": True, "0": False}
    bad = v.notna() & ~v.isin(mapping)
    if bad.any():
        raise ValueError(f"{name}: valeurs booléennes inconnues: {v[bad].value_counts().head().to_dict()}")
    return v.map(mapping).astype("boolean")


def read_data(path, start, end):
    head = pd.read_csv(path, nrows=0)
    missing = sorted(set(REQUIRED) - set(head.columns))
    if missing:
        raise ValueError(f"Colonnes manquantes : {missing}")
    d = pd.read_csv(path, usecols=REQUIRED, dtype={"Num_Acc": "string", "mode": "string", "zone": "string",
                      "highway": "string", "contexte_statut": "string", "osm_jointure": "string",
                      **{tag: "string" for tag in TAGS}}, low_memory=False)
    d["annee"] = pd.to_numeric(d["annee"], errors="raise")
    d = d.loc[d["annee"].between(start, end)].copy()
    if d.empty:
        raise ValueError("Aucune ligne dans la période")
    for col in ["cible_h2", "cible_h5_edpm", "tiers_vl", "intersection_baac_renseignee",
                "intersection_baac", "croisement_proche_point", "classe_osm_divergente"]:
        d[col] = boolean(d[col], col)
        if d[col].isna().any():
            raise ValueError(f"{col}: booléen manquant")
    d["distance_m"] = pd.to_numeric(d["distance_m"], errors="coerce")
    for col in TAGS:
        d[col] = d[col].str.strip().str.lower().replace("", pd.NA).fillna("[absent]")
    if d.duplicated(["annee", "Num_Acc", "mode"]).any():
        raise ValueError("Doublons année/accident/mode : vérifier l'unité d'analyse")
    h2 = d.loc[d["cible_h2"]].copy()
    if not h2["highway"].eq("cycleway").all():
        raise ValueError("cible_h2 contient des voies non cycleway")
    h5 = d.loc[d["cible_h5_edpm"]].copy()
    if not h5["zone"].eq("Hors agglomération").all():
        raise ValueError("cible_h5_edpm contient des cas non ruraux")
    return h2, h5


def classify_h2(d):
    d = d.copy()
    d["baac_intersection"] = "inconnue"
    d.loc[d["intersection_baac_renseignee"] & d["intersection_baac"], "baac_intersection"] = "intersection"
    d.loc[d["intersection_baac_renseignee"] & ~d["intersection_baac"], "baac_intersection"] = "hors_intersection"
    d["osm_croisement"] = "non_evalue"
    ev = d["contexte_statut"].eq("evalue").fillna(False)
    d.loc[ev & d["croisement_proche_point"], "osm_croisement"] = "croisement_proche"
    d.loc[ev & ~d["croisement_proche_point"], "osm_croisement"] = "pas_de_croisement_proche"
    return d


def combined_access(d):
    a, m = d["osm_access"], d["osm_motor_vehicle"]
    status = pd.Series("non_documente", index=d.index, dtype="string")
    access_restricted = ["no", "private"]
    conditional = ["destination", "delivery", "customers", "permissive", "permit", "agricultural", "forestry"]
    status.loc[a.isin(access_restricted)] = "restriction_access_generale"
    status.loc[a.isin(conditional)] = "access_generale_conditionnelle"
    status.loc[a.isin(["yes", "designated", "official"])] = "access_generale_ouverte"
    status.loc[~a.isin(["[absent]", *access_restricted, *conditional,
                         "yes", "designated", "official"])] = "access_generale_autre"
    status.loc[m.isin(["yes", "designated", "official"])] = "motor_vehicle_ouvert_explicite"
    status.loc[m.isin(conditional)] = "motor_vehicle_conditionnel"
    status.loc[m.isin(["no", "private"])] = "motor_vehicle_restriction_explicite"
    status.loc[~m.isin(["[absent]", "yes", "designated", "official", "no", "private", *conditional])] = "motor_vehicle_autre"
    return status


def counts(d, keys, value, label):
    out = d.groupby(keys + [value], dropna=False).size().rename("accidents").reset_index()
    return out.rename(columns={value: label})


def main():
    args = arguments()
    if args.start_year > args.end_year:
        raise ValueError("Période inversée")
    h2, h5 = read_data(args.csv, args.start_year, args.end_year)
    h2 = classify_h2(h2)
    h2["classification_access"] = combined_access(h2)
    h5["classification_access"] = combined_access(h5)
    h2["population"] = "h2_tiers_motorise"
    h2_vl = h2.loc[h2["tiers_vl"]].copy()
    h2_vl["population"] = "h2_avec_vl"
    h2 = pd.concat([h2, h2_vl], ignore_index=True)
    h5["population"] = "h5_edpm_hors_agglo_hors_cycleway"
    h5["baac_intersection"] = "sans_objet"
    h5["osm_croisement"] = "sans_objet"
    data = pd.concat([h2, h5], ignore_index=True)
    if data.empty:
        raise ValueError("Aucun cas H2/H5")
    out = args.output_dir
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"Dossier non vide : {out}; choisir --output-dir neuf")
    out.mkdir(parents=True, exist_ok=True)
    outputs = {}
    outputs["effectifs"] = data.groupby(GROUPS, dropna=False).agg(
        accidents=("Num_Acc", "size"), voies_retrouvees=("osm_jointure", lambda s: int(s.eq("retrouvee").sum())),
        classes_divergentes=("classe_osm_divergente", "sum"),
        distance_superieure_30m=("distance_m", lambda s: int(s.gt(30).sum())),
        distance_manquante=("distance_m", lambda s: int(s.isna().sum()))).reset_index()
    tagparts = []
    for tag in TAGS:
        tab = counts(data, GROUPS, tag, "valeur")
        tab.insert(len(GROUPS), "tag", tag)
        tagparts.append(tab)
    outputs["tags_par_cellule"] = pd.concat(tagparts, ignore_index=True)
    outputs["acces_par_cellule"] = counts(data, GROUPS, "classification_access", "classification_access")
    outputs["tag_motor_vehicle_x_access"] = counts(
        data, GROUPS + ["osm_motor_vehicle"], "osm_access", "osm_access")
    outputs["h5_access_par_voie"] = counts(
        h5, ["zone", "highway"], "classification_access", "classification_access") if not h5.empty else pd.DataFrame(columns=["zone", "highway", "classification_access", "accidents"])
    outputs["tag_coverage"] = pd.concat([
        data.assign(tag=tag)
        .assign(statut=lambda x: x[tag].eq("[absent]").map({True: "absent", False: "present"}))
        .groupby(BASE + ["tag", "statut"], dropna=False).size().rename("accidents").reset_index()
        for tag in TAGS], ignore_index=True)
    for name, frame in outputs.items():
        path = out / f"{name}.csv"
        frame.to_csv(path, index=False)
        print(f"{name}: {len(frame)} lignes -> {path}")
    print("Contrôle H2 urbain par mode (ne pas additionner h2_avec_vl à h2_tiers_motorise) :")
    print(outputs["effectifs"].loc[(outputs["effectifs"]["zone"] == "En agglomération") &
         (outputs["effectifs"]["population"] == "h2_tiers_motorise")]
          .groupby("mode")["accidents"].sum().to_string())
    print("Contrôle H5 rural EDPM (par voie) :")
    print(h5.groupby("highway").size().to_string() if not h5.empty else "aucun cas")


if __name__ == "__main__":
    main()
