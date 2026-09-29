#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INPUT_DIR="$ROOT/outputs/tables"
SCRIPT="$ROOT/src/analyse_gravite_h0.py"

if [[ ! -f "$SCRIPT" ]]; then
    echo "Script H0 introuvable : $SCRIPT" >&2
    exit 1
fi
if [[ ! -d "$INPUT_DIR" ]]; then
    echo "Tables d'accidents introuvables : $INPUT_DIR" >&2
    exit 1
fi

shopt -s nullglob
files=("$INPUT_DIR"/accidents-analyse-*.geoparquet)
if (( ${#files[@]} == 0 )); then
    echo "Aucune table accidents-analyse-AAAA.geoparquet dans $INPUT_DIR" >&2
    exit 1
fi

years=()
for path in "${files[@]}"; do
    name="${path##*/}"
    if [[ "$name" =~ ^accidents-analyse-([0-9]{4})\.geoparquet$ ]]; then
        years+=("${BASH_REMATCH[1]}")
    fi
done
if (( ${#years[@]} == 0 )); then
    echo "Aucune année valide détectée dans $INPUT_DIR" >&2
    exit 1
fi

IFS=$'\n' years=($(printf '%s\n' "${years[@]}" | sort -u))
unset IFS

for year in "${years[@]}"; do
    next=$((10#$year + 1))
    output="$INPUT_DIR/gravite_H0_${year}_${next}.csv"
    if [[ -e "$output" ]]; then
        echo "Sortie déjà présente, arrêt sans écrasement : $output" >&2
        exit 1
    fi
    echo "Analyse H0 [$year, $next[ → $output"
    (cd "$ROOT" && uv run python "$SCRIPT" \
        --root "$ROOT" --start-year "$((10#$year))" --end-year "$next" \
        --output "$output")
done

# Bilan couvrant toutes les années présentes seulement si elles sont contiguës.
first=$((10#${years[0]}))
last=$((10#${years[${#years[@]}-1]}))
if (( ${#years[@]} == last - first + 1 )); then
    end=$((last + 1))
    output="$INPUT_DIR/gravite_H0_${first}_${end}.csv"
    if [[ -e "$output" ]]; then
        echo "Sortie globale déjà présente, arrêt sans écrasement : $output" >&2
        exit 1
    fi
    echo "Analyse H0 globale [$first, $end[ → $output"
    (cd "$ROOT" && uv run python "$SCRIPT" \
        --root "$ROOT" --start-year "$first" --end-year "$end" \
        --output "$output")
else
    echo "Années discontinues : pas de bilan global pour éviter d'inventer des millésimes." >&2
fi
