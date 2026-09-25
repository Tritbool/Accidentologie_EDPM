#!/usr/bin/env bash
#
# Convertit chaque PBF d'un répertoire Geofabrik en GeoParquet distinct.
#
# Usage :
#   ./extract_all_roads.sh osm/raw/geofabrik_osm_190101 osm/derived/190101
#
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <pbf_directory> <derived_directory>" >&2
  exit 2
fi

PBF_DIRECTORY="$1"
DERIVED_DIRECTORY="$2"
PYTHON_SCRIPT="src/extract_osm_roads.py"

if [[ ! -d "$PBF_DIRECTORY" ]]; then
  echo "Répertoire PBF introuvable : $PBF_DIRECTORY" >&2
  exit 1
fi

if [[ ! -f "$PYTHON_SCRIPT" ]]; then
  echo "Script Python introuvable : $PYTHON_SCRIPT" >&2
  exit 1
fi

mkdir -p "$DERIVED_DIRECTORY"

while IFS= read -r -d '' pbf_path; do
  filename="$(basename "$pbf_path")"
  region="${filename%.osm.pbf}"
  output_path="${DERIVED_DIRECTORY}/${region}.geoparquet"

  if [[ -e "$output_path" ]]; then
    echo "Ignoré, sortie déjà existante : $output_path"
    continue
  fi

  echo "Extraction : $pbf_path"
  uv run python "$PYTHON_SCRIPT" \
    "$pbf_path" \
    --output "$output_path"
done < <(
  find "$PBF_DIRECTORY" \
    -maxdepth 1 \
    -type f \
    -name '*.osm.pbf' \
    -print0 \
    | sort -z
)