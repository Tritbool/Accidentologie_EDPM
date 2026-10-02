# Méthodologie de production des données et hypothèses de travail

## Objet et périmètre

Ce projet construit une table d’analyse des accidents corporels à partir des données BAAC annuelles, des voies OpenStreetMap extraites de fichiers PBF et d’un appariement géographique entre un accident et une voie OSM. La production couvre 2019 à 2024 et conserve une ligne par accident. Les comparaisons de gravité portent sur les accidents corporels recensés, et non sur un risque par trajet ou par kilomètre.

## Sources et conversion BAAC

Les quatre tables principales sont `caracteristiques`, `lieux`, `vehicules` et `usagers`. `Num_Acc` permet de les relier ; `(Num_Acc, id_vehicule)` rattache les occupants à un véhicule. Un piéton rattaché au véhicule qui l’a heurté n’est pas compté parmi les usagers de son mode. Le fichier annexe `vehicules-immat` n’entre pas dans le calcul analytique.

`src/convert_baac_to_parquet.py` lit les CSV avec Polars. `Num_Acc` est imposé en chaîne dans les quatre tables et `id_vehicule` en chaîne dans `vehicules` et `usagers`, afin d’éviter des conversions flottantes incompatibles entre millésimes. Les lignes intégralement vides sont retirées explicitement ; les clés manquantes dans des lignes non vides sont signalées. L’ingestion vérifie les identifiants, leur unicité dans les caractéristiques et les relations entre caractéristiques, véhicules et usagers non piétons avant de produire les Parquet annuels.

Le script crée aussi `BAAC/derived/<année>/caract-<année>.geoparquet` à partir des coordonnées valides en EPSG:4326. Les coordonnées sans valeur utilisable ne deviennent pas des points fictifs.

## Sources externes complémentaires

Les analyses H4, H7 et H8 mobilisent des données externes au BAAC :

- **Bilans annuels de l'ONISR (2019–2025)** : fiches « Les engins de déplacement
  personnel motorisés », panorama « La gravité des blessures » (estimations MAIS
  via le Registre du Rhône redressé), fiches « L'équipement du cycliste et de
  l'utilisateur d'EDPM ». Les effectifs ONISR sont des usagers, nos décomptes
  des accidents : les micro-écarts (630 contre 632 en 2019, périmètre incluant
  les fauteuils roulants électriques et millésimes d'extraction différents)
  sont attendus et documentés dans le document d'étude.
- **INSEE** : série 000442588 (prix moyens mensuels du gazole), moyennes
  annuelles calculées par le projet.
- **Cerema, enquêtes EMC² 2021–2023** : parts modales et distances quotidiennes
  par type de territoire.
- **Littérature technique** (H9) : essais TRL/DfT sur la stabilité des
  trottinettes selon le diamètre de roue.

## Routes OSM et appariement

`src/extract_osm_roads.py` utilise Pyrosm pour lire les PBF, ne conserve que les classes de voie prévues (`motorway`, `trunk`, `primary`, `secondary`, `tertiary`, `unclassified`, `residential`, `living_street`, `service`, `track`, `cycleway`) et produit des GeoParquet routiers avec la classe `highway`, `osm_way_id` et les attributs disponibles sur la voie.

### Scripts d'orchestration

Quatre scripts shell à la racine automatisent les étapes volumineuses :

- `geofabrik_france_outremer.sh` : télécharge les 31 extraits Geofabrik
  (métropole par région, Guadeloupe, Guyane, Martinique, Mayotte, La
  Réunion, Nouvelle-Calédonie, Polynésie française, Wallis-et-Futuna) ;
  Saint-Martin, Saint-Barthélemy et Saint-Pierre-et-Miquelon sont extraits
  par bounding box avec `osmium` depuis les PBF Amérique centrale et Canada
  (pas d'extrait Geofabrik dédié). Vérification md5, manifeste avec
  SHA-256 et mention ODbL générés automatiquement. Note : la bounding box
  de Saint-Martin couvre l'île entière, partie néerlandaise comprise.
- `extract_all_roads.sh` : applique `extract_osm_roads.py` à tous les PBF
  d'un snapshot.
- `generate_all_candidates.sh` : apparie chaque millésime BAAC au snapshot
  OSM du 1er janvier correspondant (`AA0101`), extrait par extrait, avec
  projection UTM locale estimée par extrait (`estimate_utm_crs`) et rayon
  paramétrable (`RADIUS_M`).
- `run_h0_analysis.sh` : exécute l'analyse H0 par année et, si les années
  présentes sont contiguës, le bilan global ; sorties protégées contre
  l'écrasement.

`src/bind_accident_road_candidates.py` recherche les routes à proximité de chaque accident géolocalisé dans un système métrique (par défaut EPSG:2154). Le rayon est paramétrable, par défaut 30 m. La distance point–ligne est calculée pour les voies candidates ; une seule voie est conservée par accident et par extrait OSM, celle à distance minimale, avec départage stable des ex æquo. Les points hors de l’emprise d’un extrait ne figurent pas dans sa sortie. Un `unmatched` local peut être `matched` dans un autre extrait régional. Le résultat est stocké en GeoParquet.

`src/audit_global_matches.py` compare pour chaque année les accidents BAAC géolocalisés à l’ensemble des sorties régionales. L’audit sépare `matched`, `evaluated_unmatched` et `not_evaluated`, et fournit un CSV par accident et une synthèse de couverture. Le taux de couverture ne prouve pas que la voie retenue est sémantiquement la bonne : ponts, niveaux superposés, pistes parallèles et imprécision des coordonnées restent à contrôler.

## Construction de la table d’accidents

`src/build_accident_table.py` lit les BAAC dérivés et les fichiers régionaux de routes candidates. Entre extraits, il privilégie un appariement effectif, puis la plus courte distance ; `osm_way_id` et `osm_source` départagent les ex æquo. Il vérifie l’unicité de `Num_Acc` dans la sortie et la présence des résumés véhicules et usagers. La sortie est `outputs/tables/accidents-analyse-<année>.geoparquet`.

Il conserve `n_catv_XX` pour les catégories de véhicules BAAC, `n_catv_inconnu`, les effectifs globaux d’usagers selon `grav` (1 indemne, 2 tué, 3 hospitalisé, 4 blessé léger) et les effectifs propres à chaque mode. Les usagers sans véhicule associé restent dans les totaux globaux, sans attribution arbitraire à un mode. Les piétons ne sont pas imputés à leur véhicule de rattachement.

### Définition explicite des modes et des gravités

| Préfixe de gravité | Définition `catv` | Rôle |
|---|---|---|
| `n_velo_sans_assistance_*` | `01` | Bicyclette, séparée du VAE pour H0 |
| `n_vae_*` | `80` | Vélo à assistance électrique |
| `n_edpm_*` | `50` | EDP à moteur |
| `n_edp_sans_moteur_*` | `60` | EDP sans moteur |
| `n_cyclo_*` | `02` | Cyclomoteur de moins de 50 cm³ |
| `n_velo_*` | `01` + `80` | Ancien agrégat maintenu pour compatibilité |

Pour chaque préfixe, les suffixes sont `indemnes`, `blesses_legers`, `hospitalises`, `tues` et `gravite_inconnue`. Les colonnes comptent des **personnes du mode concerné dans l’accident**, pas directement des accidents. Une comparaison H0 de la part des accidents avec au moins un usager du mode hospitalisé se calcule par : nombre d’accidents avec `n_<mode>_hospitalises > 0` divisé par le nombre d’accidents avec `n_catv_XX > 0` dans le même groupe. Une gravité manquante est conservée dans `gravite_inconnue` ; elle n’est pas assimilée à « indemne ».

Le constructeur vérifie pour chaque gravité que `n_velo_* = n_velo_sans_assistance_* + n_vae_*`. Il conserve `n_velos = n_catv_01 + n_catv_80`. La comparaison des cyclomoteurs *seulement hors agglomération* est un filtre de l’analyse H0 et non une suppression des autres accidents cyclo dans la table source.

### Sorties H0 attendues

`analyse_gravite_h0.py` est destiné à produire des lignes `année × mode × zone × type_voie` : nombre d’accidents du mode, accidents avec au moins une personne du mode dans chacun des quatre états, pourcentages rapportés aux accidents du même groupe, et nombres de personnes. Les modes comparés sont bicyclette `01`, VAE `80`, EDPM `50`, EDP sans moteur `60`, et cyclomoteur `02` uniquement hors agglomération. Ce script doit être exécuté **après** reconstruction des tables annuelles avec le schéma de gravité détaillé. Le notebook reste un outil de debug/exploration ; les résultats de production doivent être reproductibles par scripts.

## Incertitude d'échantillonnage

Toutes les parts (accidents mortels/sévères, marqueurs H9) sont des ratios k/n.
Leur incertitude d'échantillonnage est quantifiée par l'intervalle de Wilson
à 95 %, préféré à l'intervalle normal pour les petits n et les proportions
proches de 0 :

IC₉₅(k,n) = [p + z²/2n ± z√(p(1−p)/n + z²/4n²)] / (1 + z²/n),  z = 1,96

Les intervalles couvrent la variabilité d'échantillonnage uniquement :
ni le sous-enregistrement du BAAC (asymétrique — environ 70 % des blessés
EDPM chutent seuls selon le Registre du Rhône, alors que ~73 % des accidents
recensés comportent un obstacle mobile véhicule), ni l'incertitude
d'appariement, ni l'absence de dénominateur d'exposition. Un chevauchement
d'intervalles entre deux cellules signifie qu'aucun ordre n'est statistiquement
établi ; le document d'étude qualifie chaque comparaison en ce sens.

## Hypothèses de travail

Les hypothèses H0 à H9 sont examinées dans le document d'étude (voir docs/), qui fournit une réponse explicite à chacune ; cette page documente le cadre de production des données, pas les conclusions.

### Sources par hypotheses

Toutes les analyses s'exécutent depuis la racine du projet, par exemple :
`uv run python src/analyse_h9_modes_support.py --start-year 2019 --end-year 2025`
(année de fin exclue). Les paramètres par défaut de chaque script sont
listés dans son en-tête docstring.


- Les fichiers `src/analyse_gravite_h0.py`, `src/visualiser_gravite_h0_export.py`
  sont utiles aux hypothèses H0, H1, H3, H4 et H6 : le premier produit les
  comptes par année × mode × zone × classe de voie, le second le bilan annuel
  agrégé (JSON et figures), également mobilisé par l'annexe figures du
  document d'étude.
- Les fichiers `src/analyse_h2_h5.py`, `src/analyse_tags_h2_h5.py` sont utiles
  aux hypothèses H2 et H5.
- Les fichiers `src/analyse_h9_modes_support.py`, `src/analyse_h9_resultats.py`
  sont utiles à l'hypothèse H9.

### H0 — Type de voie

Le type de voie est associé à la dangerosité des accidents impliquant des EDPM et des vélos.

### H1 — Sites propres

Les accidents impliquant des vélos et des EDPM sont moins létaux sur les sites propres, notamment les voies cyclables.

La qualification de « site propre » devra être examinée : en France, certains aménagements désignés comme cyclables ne sont pas entièrement séparés de la circulation motorisée et peuvent comporter des points de conflit avec elle.

### H2 — Tiers motorisés sur sites propres

Un taux important d'accidents impliquant un tiers motorisé sur des sites propres traduit un problème d'urbanisme ou de conception de l'aménagement, plutôt qu'un problème intrinsèque au mode de déplacement.

### H3 — EDPM et cyclomoteurs hors agglomération

Hors agglomération, les EDPM présentent un profil d'accidentologie proche de celui des cyclomoteurs.

### H4 — Équipement hors agglomération

Le manque d'aménagements hors agglomération impose aux usagers de vélos et d'EDPM d'emprunter des routes partagées.

Dans ce contexte, les équipements de protection individuelle (EPI) sont plus utiles qu'ailleurs.

### H5 — Accès réglementé et conflits de circulation

EDPM hors agglomération : repérer les accidents appariés à une route OSM autre qu’une piste cyclable, puis vérifier si la circulation des EDPM y était effectivement interdite. La règle générale les limite hors agglomération aux voies vertes et pistes cyclables, mais une autorité compétente peut autoriser certaines routes limitées à 80 km/h ou moins par décision motivée. unclassified seul ne suffit donc pas à conclure « infraction ».

VL et cyclomoteurs près des voies cyclables : compter les accidents impliquant ces véhicules appariés à cycleway, puis examiner leur position et les règles d’accès locales. Un accident impliquant un VL peut avoir eu lieu à une traversée, sans que le VL ait circulé sur la piste. De même, certains cyclomoteurs peuvent être autorisés sur piste par décision locale.

### H6 — Position relative de la dangerosité des EDPM

la dangerosité attribuée aux EDPM est mal estimée relativement à celle des vélos sans assistance, des VAE et des cyclomoteurs. Le sens et l'ampleur de l'écart sont déterminés par les comparaisons, et non fixés dans l'hypothèse.

### H7 — Évolution rapportée à l'usage

Lorsque l'usage des EDPM augmente, le nombre d'accidents corporels recensés
augmente-t-il moins vite que le nombre de trajets ou de kilomètres parcourus ?
Modèle descriptif : A_t = α·E_t^γ ; γ < 1 correspondrait à une croissance
sous-linéaire. Le BAAC ne contient pas d'exposition : l'hypothèse n'est pas
testable avec les seules données du projet ; les proxys ONISR (parc, part
modale) ne suffisent pas à estimer γ.

### H8 — Ouverture encadrée des voies rurales peu fréquentées

À défaut de voie cyclable dédiée, les voies à très faible ou faible
fréquentation (OSM `unclassified`, puis `tertiary`) devraient être ouvertes
par défaut aux EDPM en zone rurale. Hypothèse normative, dérivée de H2, H3
et H5 (prémisses P1–P3) et d'un contexte de demande documenté (parc,
carburant, dépendance automobile rurale). Réponse conditionnelle : le
comparateur pertinent est l'interdiction non appliquée, pas l'absence
d'usage ; l'ouverture exige VMA cohérente, équipement effectif et suivi
de l'exposition.

### H9 — Pertinence du plafonnement uniforme à 25 km/h

Les configurations compatibles avec une interaction véhicule–support sont-elles
plus fréquentes pour les EDPM que pour les vélos, à contexte BAAC comparable ?
Le plafonnement uniforme à 25 km/h peut-il constituer à lui seul un critère de
sécurité pour des engins mécaniquement hétérogènes ?

Méthode spécifique :

- **Comparateur « vélo » = `catv 01 + 80` regroupés** (délibéré : grand diamètre
  de roue commun, et le VAE partage le plafond de 25 km/h des EDPM, ce qui
  apparie partiellement la dimension vitesse). En conséquence, les totaux
  « vélo » de H9 ne sont pas la somme des colonnes vélo et VAE de H6 : un
  accident comportant les deux catégories compte une fois.
- **Marqueurs** : `support_compatible` (composite : sans obstacle mobile
  véhicule ET au moins un signal parmi obstacle fixe, bordure, sortie de
  chaussée, surface adverse, manœuvre d'évitement) ; « sans collision »
  traité séparément. Les marqueurs décrivent des configurations codées,
  jamais des causes établies.
- **Standardisation** : comparaison dans les 369 cellules communes
  année × zone × VMA × situation × surface ; taux pondérés par l'effectif
  combiné des deux modes. Attention : la stratification porte sur la surface,
  dont `surface_adverse` est une fonction — ce marqueur est structurellement
  à parité dans le tableau standardisé et ne se compare qu'en brut.
- **VMA** : depuis 2023, la table `lieux` peut avoir plusieurs lignes par
  accident ; les sentinelles (-1, N/A, vide) sont « non renseignées » ;
  ≥2 VMA positives distinctes = « VMA multiple » ; les analyses par classe
  reposent sur les VMA uniques valides.
- **Limite de sensibilité** : le sous-enregistrement des chutes seules EDPM
  oriente le composite EDPM vers le bas, donc la comparaison vers le nul.

## Limites générales

- Le BAAC recense des accidents corporels et non tous les incidents ou chutes.
- La table ne contient pas de dénominateur d’exposition par mode et par voie ; les parts de gravité conditionnelles aux accidents ne sont pas des risques par déplacement.
- Le rattachement OSM dépend de la géolocalisation BAAC, des données OSM, de l’emprise des extraits régionaux et de la distance point–voie, sans information altimétrique.
- Les petits effectifs, particulièrement pour les EDPM hors agglomération, exigent l’affichage des nombres bruts avec les proportions.
- Les données BAAC peuvent comporter des gravités inconnues et des usagers non rattachés ; ces cas doivent rester visibles dans les contrôles de qualité.
