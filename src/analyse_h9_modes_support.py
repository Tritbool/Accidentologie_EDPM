from __future__ import annotations

"""H9 — comparaison EDPM / vélos des configurations véhicule--support.

Les résultats sont descriptifs parmi les accidents corporels BAAC. Ils ne
mesurent ni diamètre de roue, ni vitesse pratiquée, ni exposition. Le vélo
est un comparateur matériel imparfait : roues, position, pneumatiques,
géométrie et usages diffèrent simultanément.

Le marqueur ``support_compatible`` désigne une configuration BAAC compatible
avec un rôle du support, sans attribuer causalement l'accident à l'état de
voirie ou à une caractéristique technique de l'engin. Il requiert l'absence
d'obstacle mobile de type véhicule pour le mode étudié et au moins un signal
parmi : obstacle fixe, bordure, sortie de chaussée, surface adverse ou
manœuvre d'évitement. L'accident BAAC « sans collision » est conservé comme
marqueur distinct.

Exécution depuis la racine du projet :
  uv run python src/analyse_h9_modes_support.py --start-year 2019 --end-year 2025 \
      --output-dir outputs/h9_modes
L'année de fin est exclue.
"""

import argparse
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


MODES = {"edpm": {50}, "velo": {1, 80}}
GRAV = {1: "indemne", 2: "tue", 3: "hospitalise", 4: "blesse_leger"}
ZONES = {1: "Hors agglomération", 2: "En agglomération"}

FIXED_OBSTACLES = set(range(1, 18))
MOBILE_OBSTACLES = {1, 2, 4, 5, 6, 9}
FRONT_SHOCK = {1, 2, 3}
SURFACE_ADVERSE = {2, 3, 4, 5, 6, 7, 8, 9}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument(
        "--end-year",
        type=int,
        default=2025,
        help="Année exclue",
    )
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def ident(values):
    values = values.astype("string").str.strip().str.replace(
        r"\.0$",
        "",
        regex=True,
    )
    if values.isna().any() or values.eq("").any():
        raise ValueError("Identifiant vide")
    return values


def require_schema(path, columns):
    available = set(pq.read_schema(path).names)
    missing = set(columns) - available
    if missing:
        raise ValueError(f"{path}: colonnes absentes {sorted(missing)}")


def as_codes(df, columns):
    for column in columns:
        values = pd.to_numeric(df[column], errors="coerce")
        if (values.notna() & values.ne(values.round())).any():
            raise ValueError(f"Valeurs non entières dans {column}")
        df[column] = values.astype("Int64")


def normalize_lieux(lieux):
    for column in [
        "v1",
        "circ",
        "nbv",
        "vosp",
        "prof",
        "plan",
        "surf",
        "infra",
        "situ",
        "vma",
    ]:
        values = pd.to_numeric(lieux[column], errors="coerce")
        lieux[column] = values.mask(values.eq(-1)).astype("Int64")

    for column in ["lartpc", "larrout"]:
        values = pd.to_numeric(lieux[column], errors="coerce")
        lieux[column] = values.mask(values.eq(-1))

    for column in ["voie", "v2"]:
        values = lieux[column].astype("string").str.strip()
        lieux[column] = values.mask(
            values.isna()
            | values.eq("")
            | values.str.upper().isin({"N/A", "NA", "-1"})
        )

    return lieux


def aggregate_lieux(lieux, year):
    fields = ["surf", "situ", "vma"]
    grouped = lieux.groupby("Num_Acc", sort=False, dropna=False)

    nunique = grouped[fields].nunique(dropna=True)
    first = grouped[fields].first().mask(nunique.gt(1))

    result = pd.concat(
        [first, nunique.add_suffix("_n")],
        axis=1,
    ).reset_index()

    for field in fields:
        result[f"{field}_ambigue"] = result[f"{field}_n"].gt(1)

    print(
        f"{year}: lieux — "
        f"{int(result['vma_ambigue'].sum()):,} VMA multiples ; "
        f"{int(result['surf_ambigue'].sum()):,} surfaces multiples ; "
        f"{int(result['situ_ambigue'].sum()):,} situations multiples"
    )

    return result


def mode_severity(veh, users, codes, prefix):
    keys = veh.loc[
        veh["catv"].isin(codes).fillna(False),
        ["Num_Acc", "id_vehicule"],
    ]

    linked = users.merge(
        keys.assign(_mode=True),
        on=["Num_Acc", "id_vehicule"],
        how="inner",
        validate="many_to_one",
    )

    u = linked.loc[linked["catu"].isin([1, 2])].copy()

    columns = [
        f"n_usagers_{prefix}",
        f"n_{prefix}_tues",
        f"n_{prefix}_hospitalises",
        f"n_{prefix}_blesses_legers",
        f"n_{prefix}_indemnes",
        f"n_{prefix}_gravite_inconnue",
    ]

    if u.empty:
        return pd.DataFrame(columns=columns)

    u[f"n_{prefix}_tues"] = u["grav"].eq(2).astype("int64")
    u[f"n_{prefix}_hospitalises"] = u["grav"].eq(3).astype("int64")
    u[f"n_{prefix}_blesses_legers"] = u["grav"].eq(4).astype("int64")
    u[f"n_{prefix}_indemnes"] = u["grav"].eq(1).astype("int64")
    u[f"n_{prefix}_gravite_inconnue"] = (
        ~u["grav"].isin(GRAV)
    ).astype("int64")

    return u.groupby("Num_Acc", sort=False).agg(
        **{
            f"n_usagers_{prefix}": ("grav", "size"),
            f"n_{prefix}_tues": (f"n_{prefix}_tues", "sum"),
            f"n_{prefix}_hospitalises": (
                f"n_{prefix}_hospitalises",
                "sum",
            ),
            f"n_{prefix}_blesses_legers": (
                f"n_{prefix}_blesses_legers",
                "sum",
            ),
            f"n_{prefix}_indemnes": (
                f"n_{prefix}_indemnes",
                "sum",
            ),
            f"n_{prefix}_gravite_inconnue": (
                f"n_{prefix}_gravite_inconnue",
                "sum",
            ),
        }
    ).astype("Int64")


def classify_gravity(a, mode):
    nusers = f"n_usagers_{mode}"
    unknown = f"n_{mode}_gravite_inconnue"

    a["gravite"] = "non_documentee"

    a.loc[
        a[nusers].gt(0) & a[unknown].eq(a[nusers]),
        "gravite",
    ] = "inconnue_seulement"

    a.loc[
        a[f"n_{mode}_indemnes"].gt(0),
        "gravite",
    ] = "indemne_seulement"

    a.loc[
        a[f"n_{mode}_blesses_legers"].gt(0),
        "gravite",
    ] = "blesse_leger_seulement"

    a.loc[
        a[f"n_{mode}_hospitalises"].gt(0),
        "gravite",
    ] = "hospitalisation_sans_deces"

    a.loc[
        a[f"n_{mode}_tues"].gt(0),
        "gravite",
    ] = "mortel"

    return a


def mode_cases(caract, lieux, veh, users, mode, codes, year):
    selected = veh.loc[veh["catv"].isin(codes).fillna(False)].copy()

    if selected.empty:
        return pd.DataFrame()

    nveh = selected.groupby("Num_Acc").size().rename("vehicules_mode")
    sev = mode_severity(veh, users, codes, mode)

    a = (
        caract.merge(
            lieux,
            on="Num_Acc",
            how="inner",
            validate="one_to_one",
        )
        .merge(
            nveh,
            on="Num_Acc",
            how="inner",
            validate="one_to_one",
        )
        .merge(
            sev,
            on="Num_Acc",
            how="left",
            validate="one_to_one",
        )
    )

    agg = selected.groupby("Num_Acc").agg(
        n_obs_fixe=(
            "obs",
            lambda x: x.isin(FIXED_OBSTACLES).sum(),
        ),
        n_obs_bordure=(
            "obs",
            lambda x: x.eq(12).sum(),
        ),
        n_obs_chaussee=(
            "obs",
            lambda x: x.eq(14).sum(),
        ),
        n_obs_trottoir_accotement=(
            "obs",
            lambda x: x.eq(15).sum(),
        ),
        n_sortie_chaussee=(
            "obs",
            lambda x: x.eq(16).sum(),
        ),
        n_obs_mobile_vehicule=(
            "obsm",
            lambda x: x.eq(2).sum(),
        ),
        n_choc_avant=(
            "choc",
            lambda x: x.isin(FRONT_SHOCK).sum(),
        ),
        n_manoeuvre_evitement=(
            "manv",
            lambda x: x.eq(21).sum(),
        ),
    )

    a = a.merge(
        agg,
        on="Num_Acc",
        how="left",
        validate="one_to_one",
    )

    count_cols = list(sev.columns) + list(agg.columns)
    a[count_cols] = a[count_cols].fillna(0).astype("Int64")

    a["annee"] = year
    a["mode"] = mode
    a["zone"] = a["agg"].map(ZONES).fillna("Inconnue")

    valid_vma = a["vma"].where(a["vma"].gt(0))

    a["vma_classe"] = pd.cut(
        valid_vma.astype(float),
        [0, 20, 30, 40, 50, 70, 80, 90, 110, 130, float("inf")],
        labels=[
            "<=20",
            "21_30",
            "31_40",
            "41_50",
            "51_70",
            "71_80",
            "81_90",
            "91_110",
            "111_130",
            ">130",
        ],
    ).astype("string")

    a.loc[a["vma_ambigue"], "vma_classe"] = "vma_multiple"
    a["vma_classe"] = a["vma_classe"].fillna(
        "vma_non_renseignee_ou_invalide"
    )

    a["surface_classe"] = a["surf"].astype("string")
    a.loc[a["surf_ambigue"], "surface_classe"] = "surface_multiple"
    a["surface_classe"] = a["surface_classe"].fillna(
        "surface_non_renseignee"
    )

    a["situation_classe"] = a["situ"].astype("string")
    a.loc[a["situ_ambigue"], "situation_classe"] = "situation_multiple"
    a["situation_classe"] = a["situation_classe"].fillna(
        "situation_non_renseignee"
    )

    a["surface_adverse"] = a["surf"].isin(SURFACE_ADVERSE)

    a["obs_fixe"] = a["n_obs_fixe"].gt(0)
    a["obs_bordure"] = a["n_obs_bordure"].gt(0)
    a["sortie_chaussee"] = a["n_sortie_chaussee"].gt(0)

    a["obs_mobile_vehicule"] = a["n_obs_mobile_vehicule"].gt(0)
    a["choc_avant"] = a["n_choc_avant"].gt(0)
    a["manoeuvre_evitement"] = a["n_manoeuvre_evitement"].gt(0)

    a["sans_collision_baac"] = a["col"].eq(7)

    a["support_compatible"] = (
        ~a["obs_mobile_vehicule"]
        & (
            a["obs_fixe"]
            | a["obs_bordure"]
            | a["sortie_chaussee"]
            | a["surface_adverse"]
            | a["manoeuvre_evitement"]
        )
    )

    return classify_gravity(a, mode)


def read_year(root, year):
    base = root / "BAAC" / "derived" / str(year)

    cp, lp, vp, up = (
        base / f"{name}-{year}.parquet"
        for name in (
            "caracteristiques",
            "lieux",
            "vehicules",
            "usagers",
        )
    )

    for path in (cp, lp, vp, up):
        if not path.is_file():
            raise FileNotFoundError(path)

    ccols = ["Num_Acc", "agg", "col"]

    lcols = [
        "Num_Acc",
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

    vcols = [
        "Num_Acc",
        "id_vehicule",
        "catv",
        "obs",
        "obsm",
        "choc",
        "manv",
    ]

    ucols = ["Num_Acc", "id_vehicule", "catu", "grav"]

    for path, cols in (
        (cp, ccols),
        (lp, lcols),
        (vp, vcols),
        (up, ucols),
    ):
        require_schema(path, cols)

    caract = pd.read_parquet(cp, columns=ccols)
    lieux = pd.read_parquet(lp, columns=lcols)
    veh = pd.read_parquet(vp, columns=vcols)
    users = pd.read_parquet(up, columns=ucols)

    for df in (caract, lieux, veh, users):
        df["Num_Acc"] = ident(df["Num_Acc"])

    for df in (veh, users):
        df["id_vehicule"] = ident(df["id_vehicule"])

    if (
        caract["Num_Acc"].duplicated().any()
        or veh.duplicated(["Num_Acc", "id_vehicule"]).any()
    ):
        raise ValueError(f"{year}: clés BAAC dupliquées")

    as_codes(caract, ["agg", "col"])

    as_codes(
        lieux,
        [
            "v1",
            "circ",
            "nbv",
            "vosp",
            "prof",
            "plan",
            "surf",
            "infra",
            "situ",
            "vma",
        ],
    )

    as_codes(veh, ["catv", "obs", "obsm", "choc", "manv"])
    as_codes(users, ["catu", "grav"])

    lieux = normalize_lieux(lieux)
    lieux_accident = aggregate_lieux(lieux, year)

    return pd.concat(
        [
            mode_cases(
                caract,
                lieux_accident,
                veh,
                users,
                mode,
                codes,
                year,
            )
            for mode, codes in MODES.items()
        ],
        ignore_index=True,
    )


def summarize(a, groups):
    states = [
        "mortel",
        "hospitalisation_sans_deces",
        "blesse_leger_seulement",
        "indemne_seulement",
        "inconnue_seulement",
        "non_documentee",
    ]

    d = a.copy()

    for state in states:
        d[state] = d["gravite"].eq(state).astype("int64")

    metrics = [
        "obs_fixe",
        "obs_bordure",
        "obs_mobile_vehicule",
        "choc_avant",
        "manoeuvre_evitement",
        "surface_adverse",
        "sortie_chaussee",
        "sans_collision_baac",
        "support_compatible",
        "vma_ambigue",
        "surf_ambigue",
        "situ_ambigue",
    ]

    out = d.groupby(groups, dropna=False).agg(
        accidents_mode=("Num_Acc", "size"),
        vehicules_mode=("vehicules_mode", "sum"),
        **{
            state: (state, "sum")
            for state in states
        },
        **{
            metric: (metric, "sum")
            for metric in metrics
        },
        vehicules_obs_bordure=("n_obs_bordure", "sum"),
        vehicules_obs_chaussee=("n_obs_chaussee", "sum"),
        vehicules_obs_trottoir_accotement=(
            "n_obs_trottoir_accotement",
            "sum",
        ),
        vehicules_sortie_chaussee=("n_sortie_chaussee", "sum"),
    ).reset_index()

    if not out[states].sum(axis=1).eq(out["accidents_mode"]).all():
        raise ValueError("Partition de gravité invalide")

    return out


def main():
    args = arguments()

    if args.start_year >= args.end_year:
        raise ValueError("Période invalide")

    root = args.root.resolve()
    outdir = args.output_dir or root / "outputs" / "h9_modes"
    suffix = f"{args.start_year}_{args.end_year}"

    outputs = {
        "annuel": outdir / f"h9_support_par_mode_annuel_{suffix}.csv",
        "vma": outdir / f"h9_support_par_mode_vma_{suffix}.csv",
        "vma_unique": outdir / f"h9_support_par_mode_vma_unique_{suffix}.csv",
        "detail": outdir / f"h9_support_par_mode_detail_{suffix}.csv",
    }

    existing = [
        str(path)
        for path in outputs.values()
        if path.exists()
    ]

    if existing:
        raise FileExistsError(
            "Sorties déjà présentes : " + ", ".join(existing)
        )

    chunks = []

    for year in range(args.start_year, args.end_year):
        cases = read_year(root, year)
        chunks.append(cases)

        counts = cases.groupby("mode").size().to_dict()

        print(
            f"{year}: "
            + ", ".join(
                f"{mode}={n}"
                for mode, n in counts.items()
            )
        )

    all_cases = pd.concat(chunks, ignore_index=True)

    annual = summarize(
        all_cases,
        ["annee", "zone", "mode"],
    )

    vma = summarize(
        all_cases,
        ["annee", "zone", "mode", "vma_classe"],
    )

    unique_cases = all_cases.loc[
        ~all_cases["vma_ambigue"]
        & all_cases["vma"].gt(0)
    ].copy()

    vma_unique = summarize(
        unique_cases,
        ["annee", "zone", "mode", "vma_classe"],
    )

    detail = summarize(
        all_cases,
        [
            "annee",
            "zone",
            "mode",
            "vma_classe",
            "situation_classe",
            "surface_classe",
        ],
    )

    expected = (
        all_cases.groupby(
            ["annee", "zone", "mode"],
            dropna=False,
        )
        .size()
        .rename("accidents_mode")
    )

    for frame in (annual, vma, detail):
        actual = (
            frame.groupby(
                ["annee", "zone", "mode"],
                dropna=False,
            )["accidents_mode"]
            .sum()
        )

        if not actual.equals(expected):
            raise ValueError("Agrégation non exhaustive")

    outdir.mkdir(parents=True, exist_ok=True)

    for name, df in (
        ("annuel", annual),
        ("vma", vma),
        ("vma_unique", vma_unique),
        ("detail", detail),
    ):
        df.to_csv(outputs[name], index=False)
        print(outputs[name])


if __name__ == "__main__":
    main()