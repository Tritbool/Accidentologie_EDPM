from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import polars as pl
import pandas as pd
#import locale
#locale.setlocale(locale.LC_NUMERIC, 'fr_FR.UTF-8')


TABLES = ("caracteristiques", "lieux", "vehicules", "usagers","vehicules-immat")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convertit les fichiers BAAC CSV d'une année en Parquet et GeoParquet."
    )
    parser.add_argument(
        "year",
        type=int,
        help="Année BAAC à convertir, par exemple 2024.",
    )
    return parser.parse_args()


def input_path(raw_directory: Path, table: str, year: int) -> Path:
    return raw_directory / str(year) / f"{table}-{year}.csv"


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

        dataframe = pl.read_csv(
            csv_path,
            separator=";",
            infer_schema_length=10_000,
            decimal_comma=True
        )

        dataframe.write_parquet(output_directory / f"{table}-{year}.parquet")
        dataframes[table] = dataframe

        print(
            f"{table}: {dataframe.height:,} lignes, "
            f"{dataframe.width} colonnes → "
            f"{output_directory / f'{table}-{year}.parquet'}"
        )

    caract = dataframes["caracteristiques"]

    required_columns = {"lat", "long"}
    missing_columns = required_columns - set(caract.columns)

    if missing_columns:
        raise ValueError(
            "Impossible de créer le GeoParquet : colonnes manquantes dans caract : "
            f"{', '.join(sorted(missing_columns))}"
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
    
    caract_pandas["long"] = pd.to_numeric(longitude, errors="coerce")
    caract_pandas["lat"] = pd.to_numeric(latitude, errors="coerce")
    
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

    geoparquet_path = output_directory / f"caract-{year}.geoparquet"
    caract_geodataframe.to_parquet(geoparquet_path)

    print(
        f"caract GeoParquet: {len(caract_geodataframe):,} lignes → "
        f"{geoparquet_path}"
    )


if __name__ == "__main__":
    main()