#!/usr/bin/env bash
#
# Génère les candidats accident–route pour tous les millésimes BAAC
# disposant d'un GeoParquet et pour chaque extrait OSM disponible
# du snapshot correspondant.
#
# Entrées :
#   BAAC/derived/YYYY/caract-YYYY.geoparquet
#   osm/derived/YY0101/REGION-YY0101.geoparquet
#
# Sorties :
#   outputs/maps/YYYY/candidates-REGION-YYYY-YY0101.geoparquet
#
# Usage :
#   ./generate_all_candidates.sh
#   RADIUS_M=100 ./generate_all_candidates.sh
#
# Relance : une sortie existante est ignorée, jamais écrasée.
# Une paire absente est signalée ; le script continue.
#
set -euo pipefail

RADIUS_M="${RADIUS_M:-100}"
MATCHER="src/test_accident_road_candidates.py"
BAAC_ROOT="BAAC/derived"
OSM_ROOT="osm/derived"
OUTPUT_ROOT="outputs/maps"

cd "$(dirname "$0")"

if [[ ! -f "$MATCHER" ]]; then
  echo "ERREUR : script de matching introuvable : $MATCHER" >&2
  exit 1
fi

command -v uv >/dev/null 2>&1 || {
  echo "ERREUR : commande uv introuvable." >&2
  exit 1
}

if ! [[ "$RADIUS_M" =~ ^[0-9]+([.][0-9]+)?$ ]] ||
   [[ "$RADIUS_M" == "0" ]] ||
   [[ "$RADIUS_M" == "0.0" ]]; then
  echo "ERREUR : RADIUS_M doit être un nombre positif, par exemple 100." >&2
  exit 2
fi

# Un petit appel Python par extrait OSM pour déterminer son CRS métrique.
# Ce n'est PAS le matching : le fichier est ouvert uniquement pour lire
# son CRS et son emprise, sans charger les géométries dans le shell.
metric_crs_for_osm() {
  local osm_file="$1"

  uv run python - "$osm_file" <<'PY'
import sys
import geopandas as gpd
import pyarrow.parquet as pq
from shapely.geometry import box

path = sys.argv[1]

metadata = pq.read_metadata(path).metadata
if not metadata or b"geo" not in metadata:
    raise SystemExit(f"GeoParquet sans métadonnées géographiques : {path}")

# Lire seulement l'emprise des geometries avec GeoPandas nécessite,
# selon la version du GeoParquet, de charger la colonne géométrique.
# Le traitement reste séquentiel : un seul extrait à la fois.
roads = gpd.read_parquet(path, columns=["geometry"])

if roads.empty or roads.crs is None:
    raise SystemExit(f"Réseau OSM vide ou CRS manquant : {path}")

estimated = roads.estimate_utm_crs()

if estimated is None:
    raise SystemExit(f"Impossible de déterminer une projection UTM pour : {path}")

print(estimated.to_string())
PY
}

processed=0
skipped=0
missing=0
errors=0
years_found=0

shopt -s nullglob

baac_files=("$BAAC_ROOT"/[0-9][0-9][0-9][0-9]/caract-*.geoparquet)

if (( ${#baac_files[@]} == 0 )); then
  echo "ERREUR : aucun fichier BAAC trouvé dans $BAAC_ROOT." >&2
  exit 1
fi

for baac_file in "${baac_files[@]}"; do
  year="$(basename "$(dirname "$baac_file")")"
  expected_baac="$BAAC_ROOT/$year/caract-$year.geoparquet"

  if [[ "$baac_file" != "$expected_baac" ]]; then
    echo "ATTENTION : nom BAAC inattendu, ignoré : $baac_file" >&2
    ((missing += 1))
    continue
  fi

  ((years_found += 1))

  snapshot="${year:2:2}0101"
  osm_dir="$OSM_ROOT/$snapshot"
  output_dir="$OUTPUT_ROOT/$year"

  if [[ ! -d "$osm_dir" ]]; then
    echo "ABSENT : snapshot OSM $snapshot pour BAAC $year : $osm_dir" >&2
    ((missing += 1))
    continue
  fi

  osm_files=("$osm_dir"/*-"$snapshot".geoparquet)

  if (( ${#osm_files[@]} == 0 )); then
    echo "ABSENT : aucun GeoParquet OSM pour BAAC $year dans $osm_dir" >&2
    ((missing += 1))
    continue
  fi

  for osm_file in "${osm_files[@]}"; do
    filename="$(basename "$osm_file")"
    region="${filename%-${snapshot}.geoparquet}"

    if [[ -z "$region" || "$region" == "$filename" ]]; then
      echo "ATTENTION : nom OSM inattendu, ignoré : $osm_file" >&2
      ((missing += 1))
      continue
    fi

    output_file="$output_dir/candidates-$region-$year-$snapshot.geoparquet"

    if [[ -e "$output_file" ]]; then
      echo "IGNORÉ : $output_file existe déjà."
      ((skipped += 1))
      continue
    fi

    echo "TRAITEMENT : BAAC $year × OSM $region ($snapshot)"

    if ! metric_crs="$(metric_crs_for_osm "$osm_file")"; then
      echo "ÉCHEC : projection impossible pour $osm_file" >&2
      ((errors += 1))
      continue
    fi

    mkdir -p "$output_dir"

    if uv run python "$MATCHER" \
      "$baac_file" \
      "$osm_file" \
      "$output_file" \
      --radius-m "$RADIUS_M" \
      --crs-metric "$metric_crs"; then
      ((processed += 1))
    else
      echo "ÉCHEC : appariement $baac_file × $osm_file" >&2
      rm -f "$output_file"
      ((errors += 1))
    fi
  done
done

printf '\nBilan : %d année(s) BAAC ; %d produit(s) ; %d ignoré(s) ; %d absent(s)/nom incorrect ; %d erreur(s).\n' \
  "$years_found" "$processed" "$skipped" "$missing" "$errors"

if (( errors > 0 || missing > 0 )); then
  exit 1
fi