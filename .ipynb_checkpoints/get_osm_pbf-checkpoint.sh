#!/usr/bin/env bash
#
# Télécharge les extraits OSM (.osm.pbf) de Geofabrik pour la France
# métropolitaine + DROM + COM du Pacifique, à une date donnée.
#
# Usage :
#   ./geofabrik_france_outremer.sh
#   DATE_YYMMDD=240101 ./geofabrik_france_outremer.sh
#
# Variables d'environnement surchargeables :
#   DATE_YYMMDD=240101      date des snapshots (AAMMJJ, format natif
#                           Geofabrik) ou "latest"
#   OUT_DIR=./mon_dossier   répertoire de sortie
#   EXTRACT_ISLANDS=0       désactive l'extraction de Saint-Martin,
#                           Saint-Barthélemy et Saint-Pierre-et-Miquelon
#   OSMIUM_BIN=osmium       chemin du binaire osmium
#   BBOX_SAINT_MARTIN / BBOX_SAINT_BARTHELEMY / BBOX_SAINT_PIERRE_MIQUELON
#                           surcharge d'une bounding box (voir plus bas)
#
# Les snapshots historiques n'existent que pour certaines dates :
#   - "latest"            -> version la plus récente (quotidienne)
#   - 1er de chaque mois  -> ex. 240101, 250701, 260901
#   - 1er janvier         -> seules dates anciennes conservées (ex. 200101)
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Date des snapshots au format AAMMJJ (ex. 240101 = 1er janvier 2024),
# identique au nommage natif des fichiers Geofabrik.
# Mettre "latest" pour la version la plus récente.
DATE_YYMMDD="${DATE_YYMMDD:-190101}"

# Base des URL Geofabrik
BASE_URL="https://download.geofabrik.de"

# Extraits à télécharger (chemins Geofabrik, sans suffixe de date ni extension)
REGIONS=(
  "europe/france"                          # France métropolitaine (Corse incluse)
  "europe/france/guadeloupe"               # DROM
  "europe/france/guyane"                   # DROM
  "europe/france/martinique"               # DROM
  "europe/france/mayotte"                  # DROM
  "europe/france/reunion"                  # DROM
  "australia-oceania/new-caledonia"        # COM
  "australia-oceania/polynesie-francaise"  # COM
  "australia-oceania/wallis-et-futuna"     # COM
  "australia-oceania/ile-de-clipperton"    # COM
)

# ---------------------------------------------------------------------------
# Saint-Martin, Saint-Barthélemy et Saint-Pierre-et-Miquelon n'ont pas
# d'extrait dédié chez Geofabrik : ils sont extraits ici depuis les extraits
# continentaux, avec osmium.
#
# ATTENTION : ces extraits continentaux sont volumineux
# (central-america ~1,5 Go, north-america > 10 Go).
#
# 1 = télécharger les extraits continentaux et extraire les 3 territoires
# 0 = ne rien faire pour ces territoires
EXTRACT_ISLANDS="${EXTRACT_ISLANDS:-1}"

# Binaire osmium (déjà installé)
OSMIUM_BIN="${OSMIUM_BIN:-osmium}"

# Extraits continentaux sources
CONTINENTAL_REGIONS=(
  "central-america"   # contient Saint-Martin et Saint-Barthélemy
  "north-america"     # contient Saint-Pierre-et-Miquelon
)

# Bounding boxes au format osmium : lon_min,lat_min,lon_max,lat_max
# (left,bottom,right,top), en degrés décimaux WGS84.
#
# Points de repère vérifiés :
#   - Tintamarre (îlet français de Saint-Martin) : 18.119 N, -62.981 E
#   - Saint-Pierre (ville, SPM)                  : 46.779 N, -56.177 E
#   - centre du territoire SPM                   : 46.947 N, -56.262 E
#   - Île Fourchue (St-Barth)                    : ~17.98 N, -62.95 E
# Les marges sont volontairement généreuses autour de chaque archipel :
# une bbox un peu grande ne capte que de la mer, sans danger.
#
# REMARQUE Saint-Martin : l'île est franco-néerlandaise ; la bbox couvre
# l'île ENTIÈRE, partie néerlandaise (Sint Maarten) comprise — impossible
# de les séparer par bbox. Pour isoler la partie française, extraire plutôt
# avec un polygone (--polygon) construit depuis la relation OSM de la
# collectivité, ou filtrer les objets par la suite.
BBOX_SAINT_MARTIN="${BBOX_SAINT_MARTIN:--63.19,18.01,-62.94,18.15}"
BBOX_SAINT_BARTHELEMY="${BBOX_SAINT_BARTHELEMY:-63.01,17.83,-62.72,18.04}"
BBOX_SAINT_PIERRE_MIQUELON="${BBOX_SAINT_PIERRE_MIQUELON:--56.44,46.74,-56.10,47.12}"
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Préparation
# ---------------------------------------------------------------------------

if [[ "$DATE_YYMMDD" == "latest" ]]; then
  SUFFIX="latest"
else
  if [[ ! "$DATE_YYMMDD" =~ ^[0-9]{6}$ ]]; then
    echo "ERREUR : DATE_YYMMDD doit être au format AAMMJJ (ex. 240101) ou 'latest'." >&2
    exit 1
  fi
  SUFFIX="$DATE_YYMMDD"
fi

OUT_DIR="${OUT_DIR:-./geofabrik_osm_${SUFFIX}}"
mkdir -p "$OUT_DIR"

command -v curl >/dev/null 2>&1 || { echo "ERREUR : curl est requis." >&2; exit 1; }

# ---------------------------------------------------------------------------
# Fonctions
# ---------------------------------------------------------------------------

md5_of() {
  if command -v md5sum >/dev/null 2>&1; then
    md5sum "$1" | awk '{print $1}'
  else
    md5 -q "$1"  # macOS
  fi
}

check_md5() {
  local expected actual
  expected="$(awk '{print $1}' "$2")"
  actual="$(md5_of "$1")"
  [[ "$expected" == "$actual" ]]
}

download_region() {
  local region_path="$1"
  local name
  name="$(basename "$region_path")"
  local url_file="${BASE_URL}/${region_path}-${SUFFIX}.osm.pbf"
  local url_md5="${url_file}.md5"
  local out_file="${OUT_DIR}/${name}-${SUFFIX}.osm.pbf"
  local out_md5="${out_file}.md5"

  # Déjà téléchargé et intègre ? On ignore.
  if [[ -s "$out_file" && -s "$out_md5" ]] && check_md5 "$out_file" "$out_md5"; then
    echo ">> ${name} : déjà présent et valide, ignoré."
    return 0
  fi

  echo ">> ${name} : ${url_file}"
  if ! curl --fail --retry 3 --retry-delay 5 --continue-at - \
       --output "$out_file" "$url_file"; then
    rm -f "$out_file"
    echo "   ÉCHEC pour ${name}." >&2
    echo "   Astuce : cette date n'a probablement pas de snapshot." >&2
    echo "   Essayez un 1er du mois (ex. 240101) ou 'latest'." >&2
    FAILED+=("${name}")
    return 0
  fi

  if curl --fail --retry 3 --silent --output "$out_md5" "$url_md5"; then
    if check_md5 "$out_file" "$out_md5"; then
      echo "   OK (md5 vérifié)"
    else
      echo "   ATTENTION : md5 invalide pour ${name} !" >&2
      FAILED+=("${name} (md5)")
    fi
  else
    echo "   OK (md5 indisponible, non vérifié)"
  fi
}

extract_island() {
  local src_region="$1"
  local bbox="$2"
  local out_name="$3"
  local src="${OUT_DIR}/${src_region}-${SUFFIX}.osm.pbf"
  local out="${OUT_DIR}/${out_name}-${SUFFIX}.osm.pbf"

  if [[ ! -s "$src" ]]; then
    echo "   Extraction de ${out_name} impossible : ${src} est absent." >&2
    FAILED+=("${out_name} (source absente)")
    return 0
  fi

  if [[ -s "$out" ]]; then
    echo ">> ${out_name} : déjà extrait, ignoré."
    return 0
  fi

  echo ">> ${out_name} : ${OSMIUM_BIN} extract --bbox ${bbox}"
  if "$OSMIUM_BIN" extract --bbox "$bbox" --overwrite --output "$out" "$src"; then
    echo "   OK -> ${out}"
  else
    rm -f "$out"
    echo "   ÉCHEC de l'extraction de ${out_name}." >&2
    FAILED+=("${out_name} (extraction)")
  fi
}

# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------

FAILED=()

for region in "${REGIONS[@]}"; do
  download_region "$region"
done

# Territoires sans extrait Geofabrik dédié
if [[ "$EXTRACT_ISLANDS" == "1" ]]; then
  if ! command -v "$OSMIUM_BIN" >/dev/null 2>&1; then
    echo "AVERTISSEMENT : osmium introuvable (${OSMIUM_BIN}), \
extraction des îles ignorée." >&2
  else
    for region in "${CONTINENTAL_REGIONS[@]}"; do
      download_region "$region"
    done

    extract_island "central-america" "$BBOX_SAINT_MARTIN"          "saint-martin"
    extract_island "central-america" "$BBOX_SAINT_BARTHELEMY"      "saint-barthelemy"
    extract_island "north-america"   "$BBOX_SAINT_PIERRE_MIQUELON" "saint-pierre-et-miquelon"
  fi
fi

echo
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "Terminé : ${#REGIONS[@]} fichier(s) .osm.pbf dans ${OUT_DIR}/"
else
  echo "Terminé avec ${#FAILED[@]} échec(s) : ${FAILED[*]}" >&2
  exit 1
fi