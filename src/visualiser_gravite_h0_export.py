from __future__ import annotations

"""Figures H0/H1/H3 depuis BAAC-OSM et JSON compact partageable.

uv run python src/visualiser_gravite_h0.py --start-year 2019 --end-year 2025
Période [start-year, end-year[. agg BAAC : 1 hors agglo, 2 en agglo.
--json-only écrit seulement le JSON (utile si les PNG existent déjà).
La gravité est celle des usagers du mode étudié. La catégorie OSM qualifie
la voie appariée, pas la trajectoire ni une autorisation juridique.
Un accident peut concerner plusieurs modes. Pas de risque par trajet/km.
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

MODES = {
    "VELO": (1, "n_velo_sans_assistance"),
    "VAE": (80, "n_vae"),
    "EDPM": (50, "n_edpm"),
    "EDP_SANS_MOTEUR": (60, "n_edp_sans_moteur"),
    "CYCLO": (2, "n_cyclo"),
}
ZONES = {1: "Hors agglomération", 2: "En agglomération"}
RURAL_ROUTES = ["cycleway", "unclassified", "tertiary", "secondary", "primary"]
URBAN_ROUTES = ["cycleway", "living_street", "residential", "service",
                "unclassified", "tertiary", "secondary", "primary"]
ANNUAL_ROUTES = ["cycleway", "unclassified", "tertiary", "secondary", "primary", "residential"]
NON_MOTORISES = {1, 60, 80}
INDETERMINES = {0, 99}
TIERS = ["Solo (sans piéton)", "Piéton seul", "Autre véhicule du même catv seulement",
         "Autre véhicule non motorisé seulement", "Autre véhicule motorisé seulement",
         "Combinaison de tiers", "Véhicule indéterminé seulement"]
TIERS_COLORS = ["#397ca9", "#94b8d6", "#ec9846", "#f4c592", "#319449", "#9bd08d", "#929292"]
ISSUE_COLS = ["accidents_mortels", "accidents_hospitalisation_sans_deces",
              "accidents_blessure_legere_seulement", "accidents_indemne_seulement",
              "accidents_gravite_inconnue_seulement"]
ISSUES = ["Décès du mode", "Hospitalisation sans décès", "Blessure légère seulement",
          "Indemne seulement", "Gravité inconnue seulement"]
ISSUE_COLORS = ["#862c48", "#dd7846", "#e6c46a", "#83adba", "#9b9b9b"]
BILAN_FIELDS = ["accidents", *ISSUE_COLS, "victimes_tuees", "victimes_hospitalisees",
                "victimes_blessees_legeres", "usagers_gravite_inconnue"]


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start-year", type=int, default=2019)
    p.add_argument("--end-year", type=int, default=2025)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--output-dir", type=Path)
    p.add_argument("--min-n-series", type=int, default=10,
                   help="Seuil pour les pourcentages annuels affichés ; ne masque jamais les comptes JSON.")
    p.add_argument("--json-only", action="store_true", help="N'écrit que le JSON ; ne touche pas aux figures existantes.")
    return p.parse_args()


def somme_lignes(df, cols):
    return df[cols].sum(axis=1) if cols else pd.Series(0, index=df.index, dtype="int64")


def lire_accidents(root, years):
    frames = []
    for year in years:
        path = root / "outputs" / "tables" / f"accidents-analyse-{year}.geoparquet"
        if not path.is_file():
            raise FileNotFoundError(path)
        names = set(pq.read_schema(path).names)
        cat_cols = sorted(c for c in names if c.startswith("n_catv_") and c[7:].isdigit())
        required = {"Num_Acc", "agg", "highway", "n_vehicules", "n_pietons", "n_catv_inconnu", *cat_cols}
        for code, prefix in MODES.values():
            required.add(f"n_catv_{code:02d}")
            required.update(f"{prefix}_{s}" for s in
                            ("indemnes", "blesses_legers", "hospitalises", "tues", "gravite_inconnue"))
        missing = required - names
        if missing:
            raise ValueError(f"{path}: colonnes manquantes {sorted(missing)}")
        df = pd.read_parquet(path, columns=sorted(required))
        if df["Num_Acc"].isna().any() or df["Num_Acc"].duplicated().any():
            raise ValueError(f"{path}: Num_Acc manquant ou dupliqué")
        for col in required - {"Num_Acc", "agg", "highway"}:
            vals = pd.to_numeric(df[col], errors="raise")
            if vals.isna().any() or vals.lt(0).any() or vals.ne(vals.round()).any():
                raise ValueError(f"{path}: compteur invalide {col}")
            df[col] = vals.astype("int64")
        if not (df[cat_cols].sum(axis=1) + df["n_catv_inconnu"]).eq(df["n_vehicules"]).all():
            raise ValueError(f"{path}: catégories de véhicules incohérentes")
        df["annee"] = year
        df["zone"] = pd.to_numeric(df["agg"], errors="coerce").map(ZONES).fillna("Zone inconnue")
        df["type_voie"] = df["highway"].astype("string").fillna("Non apparié")
        code_cols = {int(c[7:]): c for c in cat_cols}
        for mode, (code, prefix) in MODES.items():
            sub = df.loc[df[f"n_catv_{code:02d}"].gt(0)].copy()
            if sub.empty:
                continue
            sub["mode"] = mode
            for severity in ("indemnes", "blesses_legers", "hospitalises", "tues", "gravite_inconnue"):
                sub[f"n_{severity}"] = sub[f"{prefix}_{severity}"].astype("int64")
            sub["accident_mortel"] = sub["n_tues"].gt(0)
            sub["accident_hospitalisation_sans_deces"] = sub["n_tues"].eq(0) & sub["n_hospitalises"].gt(0)
            sub["accident_blessure_legere_seulement"] = (
                sub["n_tues"].eq(0) & sub["n_hospitalises"].eq(0) & sub["n_blesses_legers"].gt(0))
            sub["accident_indemne_seulement"] = (
                sub["n_tues"].eq(0) & sub["n_hospitalises"].eq(0)
                & sub["n_blesses_legers"].eq(0) & sub["n_indemnes"].gt(0))
            sub["accident_gravite_inconnue_seulement"] = (
                sub[["n_tues", "n_hospitalises", "n_blesses_legers", "n_indemnes"]].sum(axis=1).eq(0))
            flags = pd.DataFrame({
                "pieton": sub["n_pietons"].gt(0),
                "meme": sub[f"n_catv_{code:02d}"].gt(1),
                "non_motorise": somme_lignes(sub, [v for k, v in code_cols.items()
                                                  if k in NON_MOTORISES and k != code]).gt(0),
                "motorise": somme_lignes(sub, [v for k, v in code_cols.items()
                                              if k not in NON_MOTORISES | INDETERMINES | {code}]).gt(0),
                "indetermine": (somme_lignes(sub, [v for k, v in code_cols.items()
                                                   if k in INDETERMINES]) + sub["n_catv_inconnu"]).gt(0),
            }, index=sub.index)
            count = flags.sum(axis=1)
            category = pd.Series("Combinaison de tiers", index=sub.index, dtype="string")
            category.loc[count.eq(0)] = TIERS[0]
            for flag, label in zip(flags.columns, [TIERS[1], TIERS[2], TIERS[3], TIERS[4], TIERS[6]]):
                category.loc[count.eq(1) & flags[flag]] = label
            sub["categorie_accident"] = category
            cols = ["annee", "mode", "zone", "type_voie", "categorie_accident",
                    "n_tues", "n_hospitalises", "n_blesses_legers", "n_indemnes",
                    "n_gravite_inconnue", "accident_mortel", "accident_hospitalisation_sans_deces",
                    "accident_blessure_legere_seulement", "accident_indemne_seulement",
                    "accident_gravite_inconnue_seulement"]
            frames.append(sub[cols])
    if not frames:
        raise ValueError("Aucun accident des modes sélectionnés dans la période")
    return pd.concat(frames, ignore_index=True)


def bilan(df, by):
    if df.empty:
        return pd.DataFrame(columns=[*by, *BILAN_FIELDS])
    result = df.groupby(by, dropna=False, observed=True).agg(
        accidents=("mode", "size"), accidents_mortels=("accident_mortel", "sum"),
        accidents_hospitalisation_sans_deces=("accident_hospitalisation_sans_deces", "sum"),
        accidents_blessure_legere_seulement=("accident_blessure_legere_seulement", "sum"),
        accidents_indemne_seulement=("accident_indemne_seulement", "sum"),
        accidents_gravite_inconnue_seulement=("accident_gravite_inconnue_seulement", "sum"),
        victimes_tuees=("n_tues", "sum"), victimes_hospitalisees=("n_hospitalises", "sum"),
        victimes_blessees_legeres=("n_blesses_legers", "sum"),
        usagers_gravite_inconnue=("n_gravite_inconnue", "sum"),
    ).reset_index()
    if not result[ISSUE_COLS].sum(axis=1).eq(result["accidents"]).all():
        raise ValueError("Partition de gravité non exhaustive")
    return result


def modes_zone(zone):
    return list(MODES) if zone == "Hors agglomération" else [m for m in MODES if m != "CYCLO"]


def table_tiers(data, zone, routes, out):
    modes = modes_zone(zone)
    s = data.loc[data["zone"].eq(zone) & data["type_voie"].isin(routes) & data["mode"].isin(modes)]
    fig, axes = plt.subplots(1, len(routes), figsize=(4.2 * len(routes) + 3, 6.9), sharey=True)
    for ax, road in zip(np.atleast_1d(axes), routes):
        d = s.loc[s["type_voie"].eq(road)]
        table = pd.crosstab(d["mode"], d["categorie_accident"]).reindex(
            index=modes, columns=TIERS, fill_value=0)
        n = table.sum(axis=1)
        pct = table.div(n.replace(0, np.nan), axis=0).fillna(0) * 100
        left = np.zeros(len(modes))
        for category, color in zip(TIERS, TIERS_COLORS):
            ax.barh(np.arange(len(modes)), pct[category], left=left, label=category, color=color)
            left += pct[category].to_numpy()
        ax.set_yticks(np.arange(len(modes)), [f"{mode} (n={int(n[mode])})" for mode in modes])
        ax.invert_yaxis(); ax.set_xlim(0, 100)
        ax.set_xlabel("Part des accidents (%)"); ax.set_title(road); ax.grid(axis="x", alpha=.15)
    handles, labels = np.atleast_1d(axes)[-1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, fontsize=8, frameon=False)
    fig.suptitle(f"{zone} : nature des accidents par mode et voie OSM")
    fig.text(.5, .08, "Tiers motorisé = tous véhicules motorisés, pas seulement VL ; cycleway = classe OSM, pas preuve de séparation effective.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .16, 1, .94)); fig.savefig(out, dpi=190); plt.close(fig)


def issue_par_voie(data, zone, routes, out):
    modes = modes_zone(zone)
    d = bilan(data.loc[data["zone"].eq(zone) & data["type_voie"].isin(routes)
                       & data["mode"].isin(modes)], ["type_voie", "mode"])
    fig, axes = plt.subplots(1, len(routes), figsize=(5.2 * len(routes) + 3, 7.3), sharey=True)
    axes = np.atleast_1d(axes)
    for ax, road in zip(axes, routes):
        lookup = d.loc[d["type_voie"].eq(road)].set_index("mode")
        totals = np.array([int(lookup.loc[m, "accidents"]) if m in lookup.index else 0 for m in modes])
        bottom = np.zeros(len(modes))
        for col, label, color in zip(ISSUE_COLS, ISSUES, ISSUE_COLORS):
            counts = np.array([int(lookup.loc[m, col]) if m in lookup.index else 0 for m in modes])
            pct = np.divide(100 * counts, totals, out=np.zeros(len(modes), dtype=float), where=totals > 0)
            ax.bar(np.arange(len(modes)), pct, bottom=bottom, width=.78, color=color, label=label)
            bottom += pct
        ax.set_xticks(np.arange(len(modes)), [f"{m}\n(n={n})" for m, n in zip(modes, totals)], rotation=35, ha="right")
        ax.set_ylim(0, 100); ax.set_title(road); ax.grid(axis="y", alpha=.2)
    axes[0].set_ylabel("Part des accidents corporels du mode (%)")
    handles, labels = axes[-1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, fontsize=9, frameon=False)
    fig.suptitle(f"{zone} : gravité maximale des usagers par accident, mode et voie")
    fig.text(.5, .09, "Classes exclusives ; décès prioritaire. n = accidents du mode ; n=0 = absence d'observations, pas 0 % de décès.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .17, 1, .94)); fig.savefig(out, dpi=190); plt.close(fig)


def deces_relatifs_modes(data, zone, routes, out):
    modes = modes_zone(zone)
    d = bilan(data.loc[data["zone"].eq(zone) & data["type_voie"].isin(routes)
                       & data["mode"].isin(modes)], ["type_voie", "mode"])
    fig, axes = plt.subplots(1, len(routes), figsize=(4.3 * len(routes) + 3, 5.8), sharey=True)
    axes = np.atleast_1d(axes)
    rates = []
    for ax, road in zip(axes, routes):
        lookup = d.loc[d["type_voie"].eq(road)].set_index("mode")
        for i, mode in enumerate(modes):
            if mode not in lookup.index:
                continue
            n = int(lookup.loc[mode, "accidents"])
            deaths = int(lookup.loc[mode, "accidents_mortels"])
            if n:
                value = 100 * deaths / n
                rates.append(value); ax.scatter(i, value, color="#862c48", s=45, zorder=3)
                ax.annotate(f"{deaths}/{n}", (i, value), xytext=(0, 7),
                            textcoords="offset points", ha="center", fontsize=8)
        ax.set_xticks(np.arange(len(modes)), modes, rotation=45, ha="right")
        ax.set_title(road); ax.grid(axis="y", alpha=.2)
    axes[0].set_ylabel("Accidents avec décès du mode / accidents du mode (%)")
    for ax in axes:
        ax.set_ylim(0, max(5, max(rates, default=0) * 1.35))
    fig.suptitle(f"{zone} : part d'accidents mortels selon le mode et la voie")
    fig.text(.5, .01, "Étiquette = accidents mortels / accidents corporels du mode ; petit n = estimation instable. Pas un risque par trajet.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .06, 1, .92)); fig.savefig(out, dpi=190); plt.close(fig)


def bilan_annuel_modes(data, years, out):
    d = bilan(data.loc[data["zone"].isin(ZONES.values())], ["annee", "zone", "mode"])
    fig, axes = plt.subplots(2, 3, figsize=(17, 10), sharex=True)
    fields = [("accidents_mortels", "Accidents avec décès du mode"),
              ("accidents_hospitalisation_sans_deces", "Accidents avec hospitalisation\nsans décès du mode"),
              ("accidents", "Accidents corporels du mode")]
    for row, zone in enumerate(("Hors agglomération", "En agglomération")):
        for col, (field, ylabel) in enumerate(fields):
            ax = axes[row, col]
            for mode in modes_zone(zone):
                seq = d.loc[d["zone"].eq(zone) & d["mode"].eq(mode)].set_index("annee").reindex(years)[field].fillna(0)
                ax.plot(years, seq, marker="o", label=mode)
            ax.set_title(f"{zone} · {ylabel}"); ax.set_xticks(years); ax.grid(alpha=.2)
            if col == 0:
                ax.set_ylabel("Accidents")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(MODES), frameon=False)
    fig.suptitle("Bilans annuels par mode : décès, hospitalisations et accidents")
    fig.tight_layout(rect=(0, .07, 1, .96)); fig.savefig(out, dpi=190); plt.close(fig)


def evolution_gravite_modes(data, years, out, min_n):
    s = data.loc[data["type_voie"].isin(ANNUAL_ROUTES) & data["zone"].isin(ZONES.values())]
    d = bilan(s, ["annee", "zone", "type_voie", "mode"])
    fig, axes = plt.subplots(2, 3, figsize=(17, 9), sharex=True, sharey=True)
    for ax, road in zip(axes.flat, ANNUAL_ROUTES):
        sub = d.loc[d["type_voie"].eq(road)]
        for mode in MODES:
            for zone, linestyle in (("Hors agglomération", "--"), ("En agglomération", "-")):
                seq = sub.loc[sub["mode"].eq(mode) & sub["zone"].eq(zone)].set_index("annee").reindex(years)
                n = seq["accidents"]
                severe = seq["accidents_mortels"].fillna(0) + seq["accidents_hospitalisation_sans_deces"].fillna(0)
                pct = (100 * severe / n).where(n.ge(min_n))
                ax.plot(years, pct, linestyle=linestyle, marker="o", markersize=3, label=f"{mode} · {zone}")
        ax.set_title(road); ax.set_xticks(years); ax.grid(alpha=.2)
    for ax in axes[:, 0]:
        ax.set_ylabel("Accidents avec usager du mode\nhospitalisé ou tué (%)")
    for ax in axes[-1, :]:
        ax.set_xlabel("Année")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, fontsize=8, frameon=False)
    fig.suptitle("Évolution annuelle de la gravité sévère par voie, mode et zone")
    fig.text(.5, .07, f"Lignes pleines = en agglomération, tirets = hors agglomération ; points si n ≥ {min_n}. Comptes exacts dans le JSON annuel.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .14, 1, .96)); fig.savefig(out, dpi=190); plt.close(fig)


def rural_years(data, years, out):
    routes = ["cycleway", "unclassified", "tertiary"]
    d = bilan(data.loc[data["zone"].eq("Hors agglomération") & data["type_voie"].isin(routes)],
              ["annee", "mode", "type_voie"])
    fig, axes = plt.subplots(len(MODES), 1, figsize=(11, 11), sharex=True)
    for ax, mode in zip(axes, MODES):
        sub = d.loc[d["mode"].eq(mode)]
        for road in routes:
            seq = sub.loc[sub["type_voie"].eq(road)].set_index("annee").reindex(years)["accidents"].fillna(0)
            ax.plot(years, seq, marker="o", label=road)
        ax.set_ylabel(mode); ax.grid(alpha=.25); ax.legend(fontsize=8)
    axes[-1].set_xticks(years); axes[-1].set_xlabel("Année")
    fig.suptitle("Accidents corporels hors agglomération par mode et classe OSM")
    fig.tight_layout(rect=(0, 0, 1, .97)); fig.savefig(out, dpi=190); plt.close(fig)


def record_bilan(row, keys):
    return {**{key: (int(row[key]) if key == "annee" else str(row[key])) for key in keys},
            **{field: int(row[field]) for field in BILAN_FIELDS}}


def records_bilan(frame, keys):
    return [record_bilan(row, keys) for _, row in frame.sort_values(keys).iterrows()]


def export_json(data, years, out, min_n):
    zones = list(ZONES.values())
    usable = data.loc[data["zone"].isin(zones)]
    route_sets = {"Hors agglomération": RURAL_ROUTES, "En agglomération": URBAN_ROUTES}
    selected = pd.concat([usable.loc[usable["zone"].eq(zone)
                                        & usable["type_voie"].isin(routes)
                                        & usable["mode"].isin(modes_zone(zone))]
                          for zone, routes in route_sets.items()], ignore_index=True)
    by_road = bilan(selected, ["zone", "type_voie", "mode"])
    by_year_zone = bilan(usable, ["annee", "zone", "mode"])
    by_year_road = bilan(usable.loc[usable["type_voie"].isin(ANNUAL_ROUTES)],
                          ["annee", "zone", "type_voie", "mode"])
    tier_counts = selected.groupby(["zone", "type_voie", "mode", "categorie_accident"], observed=True).size()
    tier_total = selected.groupby(["zone", "type_voie", "mode"], observed=True).size()
    tier_rows = []
    for (zone, road, mode), n in tier_total.sort_index().items():
        cats = {cat: int(tier_counts.get((zone, road, mode, cat), 0)) for cat in TIERS}
        if sum(cats.values()) != int(n):
            raise ValueError("Catégories de tiers incohérentes")
        tier_rows.append({"zone": zone, "type_voie": road, "mode": mode,
                          "accidents": int(n), "categories": cats})
    obj = {
        "metadata": {
            "annees_incluses": years, "zones_baac": {"1": ZONES[1], "2": ZONES[2]},
            "modes": list(MODES), "voies_par_zone": route_sets,
            "unite_bilan": "accidents avec au moins un véhicule du mode ; décompte des usagers distinct",
            "unite_tiers": "accidents, catégorie exclusive par mode",
            "gravite": "gravité maximale connue parmi les usagers du mode ; cinq classes exclusives",
            "pourcentages": "calculer à partir des comptes (accidents_mortels/accidents, etc.)",
            "seuil_affichage_pourcentage_annuel": min_n,
            "seuil_export": "aucun ; petits effectifs et zéros observés conservés",
            "attention": "Un accident peut figurer dans plusieurs modes. OSM=voie appariée, pas trajectoire. Pas de risque par trajet/km. Cyclo urbain exclu des figures mais inclus dans bilans annuels et annuels par voie. Les voies non appariées/zone inconnue ne figurent pas dans ces graphiques."
        },
        "composition_tiers_par_voie": tier_rows,
        "gravite_par_voie": records_bilan(by_road, ["zone", "type_voie", "mode"]),
        "bilan_annuel_modes": records_bilan(by_year_zone, ["annee", "zone", "mode"]),
        "evolution_gravite_par_voie": records_bilan(by_year_road, ["annee", "zone", "type_voie", "mode"]),
    }
    with out.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return obj


def main():
    args = arguments()
    if args.start_year >= args.end_year or args.min_n_series < 1:
        raise ValueError("Période ou --min-n-series invalide")
    root = args.root.resolve()
    output = args.output_dir or root / "outputs" / "figures" / "h0"
    years = list(range(args.start_year, args.end_year))
    suffix = f"{args.start_year}_{args.end_year}"
    names = ["composition_tiers_urbaine", "composition_tiers_rurale",
             "gravite_modes_urbaine", "gravite_modes_rurale",
             "deces_relatifs_modes_urbain", "deces_relatifs_modes_rural",
             "bilan_annuel_modes", "evolution_gravite_modes", "rural_evolution"]
    paths = {name: output / f"{name}_{suffix}.png" for name in names}
    json_path = output / f"donnees_graphiques_{suffix}.json"
    targets = [json_path] if args.json_only else [*paths.values(), json_path]
    existing = [str(path) for path in targets if path.exists()]
    if existing:
        raise FileExistsError("Sorties déjà présentes ; choisir --output-dir ou déplacer : " + ", ".join(existing))
    data = lire_accidents(root, years)
    output.mkdir(parents=True, exist_ok=True)
    export_json(data, years, json_path, args.min_n_series)
    print(json_path)
    if args.json_only:
        return
    table_tiers(data, "En agglomération", URBAN_ROUTES, paths["composition_tiers_urbaine"])
    table_tiers(data, "Hors agglomération", RURAL_ROUTES, paths["composition_tiers_rurale"])
    issue_par_voie(data, "En agglomération", URBAN_ROUTES, paths["gravite_modes_urbaine"])
    issue_par_voie(data, "Hors agglomération", RURAL_ROUTES, paths["gravite_modes_rurale"])
    deces_relatifs_modes(data, "En agglomération", URBAN_ROUTES, paths["deces_relatifs_modes_urbain"])
    deces_relatifs_modes(data, "Hors agglomération", RURAL_ROUTES, paths["deces_relatifs_modes_rural"])
    bilan_annuel_modes(data, years, paths["bilan_annuel_modes"])
    evolution_gravite_modes(data, years, paths["evolution_gravite_modes"], args.min_n_series)
    rural_years(data, years, paths["rural_evolution"])
    for path in paths.values():
        print(path)


if __name__ == "__main__":
    main()
