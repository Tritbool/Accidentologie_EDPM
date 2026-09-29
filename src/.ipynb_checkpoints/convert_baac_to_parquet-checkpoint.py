from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
import polars as pl


TABLES = (
    "caracteristiques",
    "lieux",
    "vehicules",
    "usagers",
    #"vehicules-immat",
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convertit les fichiers BAAC CSV d'une année "
            "en Parquet et GeoParquet."
        )
    )
    parser.add_argument(
        "year",
        type=int,
        help="Année BAAC à convertir, par exemple 2024.",
    )
    return parser.parse_args()


def input_path(raw_directory: Path, table: str, year: int) -> Path:
    return raw_directory / str(year) / f"{table}-{year}.csv"


def verifier_num_acc(
    table: str,
    dataframe: pl.DataFrame,
    year: int,
) -> None:
    if "Num_Acc" not in dataframe.columns:
        raise ValueError(f"{table}-{year} : colonne Num_Acc absente")

    if dataframe.schema["Num_Acc"] != pl.String:
        raise TypeError(
            f"{table}-{year} : Num_Acc doit être de type String, "
            f"pas {dataframe.schema['Num_Acc']}"
        )

    if dataframe["Num_Acc"].is_null().any():
        raise ValueError(
            f"{table}-{year} : Num_Acc contient des valeurs nulles"
        )

    invalide = dataframe.select(
        pl.col("Num_Acc").str.strip_chars().str.contains(
            r"^[0-9]{12}$"
        ).not_().sum()
    ).item()
    if invalide:
        raise ValueError(
            f"{table}-{year} : {invalide} Num_Acc ne sont pas "
            "des identifiants à 12 chiffres"
        )


def verifier_couverture(
    nom: str,
    ids: pl.DataFrame,
    references: pl.DataFrame,
) -> None:
    manquants = ids.select("Num_Acc").unique().join(
        references.select("Num_Acc").unique(),
        on="Num_Acc",
        how="anti",
    )
    if manquants.height:
        raise ValueError(
            f"{nom} : {manquants.height} Num_Acc absents "
            f"des caractéristiques ; exemples : "
            f"{manquants['Num_Acc'].head(5).to_list()}"
        )


def main() -> None:
    args = parse_arguments()
    year = args.year

    raw_directory = Path("BAAC/raw")
    output_directory = Path("BAAC/derived") / str(year)
    output_directory.mkdir(parents=True, exist_ok=True)

    dataframes: dict[str, pl.DataFrame] = {}

    for table in TABLES:
        csv_path = input_path(raw_directory, table, year)
        if not csv_path.is_file():
            raise FileNotFoundError(
                f"Fichier BAAC introuvable : {csv_path}\n"
                f"Attendu : BAAC/raw/{year}/{table}-{year}.csv"
            )

        overrides = {"Num_Acc": pl.String}
        if table in {"vehicules", "usagers"}:#, "vehicules-immat"}:
            overrides["id_vehicule"] = pl.String

        dataframe = pl.read_csv(
            csv_path,
            separator=";",
            infer_schema_length=10_000,
            decimal_comma=True,
            schema_overrides=overrides,
        )

        lignes_vides = dataframe.select(
            pl.all_horizontal(pl.all().is_null()).sum()
        ).item()
        
        if lignes_vides:
            print(
                f"{table}-{year} : "
                f"{lignes_vides} ligne(s) entièrement vide(s) ignorée(s)"
            )
            dataframe = dataframe.filter(
                ~pl.all_horizontal(pl.all().is_null())
            )
        
        verifier_num_acc(table, dataframe, year)
        
        verifier_num_acc(table, dataframe, year)
        dataframes[table] = dataframe

    caract = dataframes["caracteristiques"]
    vehicules = dataframes["vehicules"]
    usagers = dataframes["usagers"]

    if caract["Num_Acc"].n_unique() != caract.height:
        raise ValueError(
            f"caracteristiques-{year} : Num_Acc dupliqués"
        )

    for table in ("lieux", "vehicules", "usagers"):#, "vehicules-immat"):
        verifier_couverture(
            f"{table}-{year}",
            dataframes[table],
            caract,
        )

    for table in ("vehicules", "usagers"):
        dataframe = dataframes[table]
        for col in ("id_vehicule",):
            if col not in dataframe.columns:
                raise ValueError(
                    f"{table}-{year} : colonne {col} absente"
                )
            if dataframe[col].is_null().any():
                raise ValueError(
                    f"{table}-{year} : {col} contient des valeurs nulles"
                )

    if "catu" not in usagers.columns:
        raise ValueError(f"usagers-{year} : colonne catu absente")

    cles_veh = vehicules.select(
        "Num_Acc", "id_vehicule"
    ).unique()
    if cles_veh.height != vehicules.height:
        raise ValueError(
            f"vehicules-{year} : couples "
            "(Num_Acc, id_vehicule) dupliqués"
        )

    usagers_non_pietons = usagers.filter(
        pl.col("catu").cast(pl.Int64, strict=False).is_in([1, 2])
    )
    sans_vehicule = usagers_non_pietons.join(
        cles_veh,
        on=["Num_Acc", "id_vehicule"],
        how="anti",
    )
    if sans_vehicule.height:
        raise ValueError(
            f"usagers-{year} : {sans_vehicule.height} usagers "
            "non piétons sans véhicule correspondant ; "
            f"exemples : {sans_vehicule.select(
                'Num_Acc', 'id_vehicule'
            ).head(5).to_dicts()}"
        )

    required_columns = {"lat", "long"}
    missing_columns = required_columns - set(caract.columns)
    if missing_columns:
        raise ValueError(
            "Impossible de créer le GeoParquet : "
            "colonnes manquantes dans caract : "
            f"{', '.join(sorted(missing_columns))}"
        )

    # Écriture seulement après validation des tables.
    for table, dataframe in dataframes.items():
        output_path = output_directory / f"{table}-{year}.parquet"
        dataframe.write_parquet(output_path)
        print(
            f"{table}: {dataframe.height:,} lignes, "
            f"{dataframe.width} colonnes → {output_path}"
        )

    caract_pandas = caract.to_pandas()

    longitude = (
        caract_pandas["long"]
        .astype("string")
        .str.replace(",", ".", regex=False)
    )
    latitude = (
        caract_pandas["lat"]
        .astype("string")
        .str.replace(",", ".", regex=False)
    )

    caract_pandas["long"] = pd.to_numeric(
        longitude, errors="coerce"
    )
    caract_pandas["lat"] = pd.to_numeric(
        latitude, errors="coerce"
    )

    valid_coordinates = (
        caract_pandas["long"].between(-180, 180)
        & caract_pandas["lat"].between(-90, 90)
    )

    caract_geodataframe = gpd.GeoDataFrame(
        caract_pandas.loc[valid_coordinates].copy(),
        geometry=gpd.points_from_xy(
            caract_pandas.loc[valid_coordinates, "long"],
            caract_pandas.loc[valid_coordinates, "lat"],
        ),
        crs="EPSG:4326",
    )

    geoparquet_path = (
        output_directory / f"caract-{year}.geoparquet"
    )
    caract_geodataframe.to_parquet(geoparquet_path)

    print(
        f"caract GeoParquet: "
        f"{len(caract_geodataframe):,} lignes → {geoparquet_path}"
    )


if __name__ == "__main__":
    main()