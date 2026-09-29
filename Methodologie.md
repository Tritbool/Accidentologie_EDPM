# Méthodologie de production des données et hypothèses de travail

## Objet et périmètre

Ce projet construit une base d’analyse des accidents corporels de la circulation à partir :

- des données annuelles BAAC ;
- de données routières OpenStreetMap (OSM) extraites depuis des fichiers PBF ;
- d’un appariement géographique entre chaque accident BAAC géolocalisé et une voie OSM candidate.

L’objectif de la chaîne est de produire une table plate, à raison d’une ligne par accident, enrichie par :

- les caractéristiques générales de l’accident ;
- les véhicules impliqués, notamment leurs catégories BAAC ;
- les usagers et leur gravité ;
- une voie OSM candidate, sa classe `highway` et la distance entre l’accident et cette voie.

La chaîne est construite pour les années BAAC 2019 à 2024. Les résultats d’analyse doivent toujours être interprétés comme des résultats **parmi les accidents corporels enregistrés et appariés**, et non comme des taux de risque par déplacement, par kilomètre ou par heure de pratique.

## Sources BAAC

Les données BAAC annuelles sont organisées en quatre tables principales :

- `caracteristiques` : circonstances générales et géolocalisation de l’accident ;
- `lieux` : caractéristiques de la voie et du lieu ;
- `vehicules` : véhicules impliqués et catégorie BAAC `catv` ;
- `usagers` : usagers impliqués, rôle `catu` et gravité `grav`.

Les jointures suivent le schéma BAAC :

- `Num_Acc` relie les quatre tables au niveau de l’accident ;
- le couple `(Num_Acc, id_vehicule)` relie les véhicules à leurs occupants ;
- les piétons sont rattachés dans la table des usagers au véhicule qui les a heurtés ; ils ne doivent donc pas être imputés à la catégorie de ce véhicule lors du calcul des gravités par mode.

Le fichier annexe `vehicules-immat` n’est pas utilisé dans la chaîne analytique principale : il ne fait pas partie du schéma officiel de jointure entre les quatre tables BAAC et ne fournit pas les informations nécessaires à la catégorisation ou à la gravité par mode.

## Conversion des CSV BAAC

### Objectif

Les CSV bruts sont convertis en Parquet afin de stabiliser le schéma, d’accélérer les lectures sélectives et de produire un GeoParquet des accidents géolocalisés.

### Lecture et normalisation des identifiants

Les fichiers CSV sont lus avec Polars. Les identifiants relationnels sont imposés comme chaînes de caractères dès l’ingestion :

- `Num_Acc` est lu comme `String` dans toutes les tables BAAC principales ;
- `id_vehicule` est lu comme `String` dans `vehicules` et `usagers`.

Cette contrainte évite que l’inférence de type transforme selon les années certains identifiants en `Float64`, par exemple `201900000024.0`, alors que d’autres fichiers les représentent comme chaînes sans décimale, par exemple `201900000024`. Une telle divergence casse silencieusement les jointures accident–véhicule–usager.

Une ligne entièrement vide en fin de fichier CSV est supprimée explicitement. En revanche, une ligne partiellement renseignée sans `Num_Acc` reste une anomalie bloquante : elle n’est pas supprimée automatiquement.

### Contrôles de cohérence BAAC

Avant écriture des sorties, la conversion applique les contrôles suivants :

- `Num_Acc` doit être présent, non nul et conforme à un identifiant numérique à 12 chiffres ;
- `Num_Acc` doit être unique dans `caracteristiques` ;
- tous les accidents de `lieux`, `vehicules` et `usagers` doivent être présents dans `caracteristiques` ;
- `id_vehicule` doit être renseigné dans `vehicules` et `usagers` ;
- le couple `(Num_Acc, id_vehicule)` doit être unique dans `vehicules` ;
- chaque usager non piéton doit retrouver un véhicule correspondant par `(Num_Acc, id_vehicule)`.

Ces contrôles font échouer l’ingestion en cas d’incohérence plutôt que de produire des valeurs manquantes susceptibles d’être interprétées ensuite comme des zéros.

### Sorties BAAC

Pour chaque année, la conversion produit :

- `BAAC/derived/<année>/caracteristiques-<année>.parquet`
- `BAAC/derived/<année>/lieux-<année>.parquet`
- `BAAC/derived/<année>/vehicules-<année>.parquet`
- `BAAC/derived/<année>/usagers-<année>.parquet`
- `BAAC/derived/<année>/caract-<année>.geoparquet`

Le GeoParquet `caract-<année>.geoparquet` est construit à partir de `lat` et `long`, converties en numériques après remplacement de la virgule décimale par un point. Seules les coordonnées comprises dans les bornes géographiques mondiales sont conservées. Le système de coordonnées est EPSG:4326.

## Construction de la table d’accidents

### Principe

La table d’analyse finale conserve un grain strict : **une ligne par accident BAAC**. Elle est construite annuellement puis enregistrée dans :

```text
outputs/tables/accidents-analyse-<année>.geoparquet
```

Elle combine :

- l’accident BAAC ;
- la voie OSM retenue après appariement ;
- les effectifs de véhicules par catégorie `catv` ;
- les effectifs d’usagers par gravité ;
- les effectifs d’usagers par mode et par gravité.

### Normalisation défensive

La table de construction conserve un normaliseur de sécurité pour `Num_Acc` et `id_vehicule`, même si les Parquet dérivés doivent déjà être corrects :

- `Num_Acc` est converti en entier nullable puis en chaîne ;
- `id_vehicule` est nettoyé des espaces et d’un éventuel suffixe `.0`.

Ce normaliseur est un garde-fou de lecture. La normalisation principale doit rester faite à l’ingestion des CSV BAAC.

### Catégories de véhicules

La table contient des compteurs `n_catv_XX` pour les catégories BAAC documentées entre 2019 et 2024, par exemple :

- `n_catv_01` : bicyclette ;
- `n_catv_02` : cyclomoteur de moins de 50 cm³ ;
- `n_catv_07` : véhicule léger ;
- `n_catv_10` : véhicule utilitaire ;
- `n_catv_50` : EDP à moteur ;
- `n_catv_60` : EDP sans moteur ;
- `n_catv_80` : vélo à assistance électrique ;
- `n_catv_99` : autre véhicule.

Les véhicules de catégorie inconnue ou non documentée sont comptés dans `n_catv_inconnu`.

Des agrégats utiles sont ensuite dérivés :

- `n_edpm = n_catv_50` ;
- `n_velos = n_catv_01 + n_catv_80` ;
- `n_vl = n_catv_07` ;
- `n_tiers_vehicules_edpm` : nombre de véhicules autres que les EDPM dans un accident impliquant au moins un EDPM.

### Gravité des usagers

Les gravités BAAC sont agrégées au niveau accident :

- `n_indemnes` ;
- `n_tues` ;
- `n_hospitalises` ;
- `n_blesses_legers` ;
- `n_gravite_inconnue`.

Pour les modes ciblés, les usagers sont joints à leur véhicule par `(Num_Acc, id_vehicule)`. Les piétons sont exclus de cette attribution par mode, afin de ne pas les compter comme occupants du véhicule qui les a heurtés.

Les compteurs spécifiques de mode incluent notamment :

- `n_edpm_indemnes`, `n_edpm_tues`, `n_edpm_hospitalises`, `n_edpm_blesses_legers` ;
- `n_velo_indemnes`, `n_velo_tues`, `n_velo_hospitalises`, `n_velo_blesses_legers`.

Les usagers sans véhicule retrouvé sont conservés dans les effectifs globaux d’usagers, mais ne sont pas attribués à un mode. Cette décision évite d’inventer une catégorie de véhicule ou une gravité spécifique au mode.

### Choix de la voie OSM retenue

Plusieurs extraits régionaux OSM peuvent fournir des candidats pour un même accident. La table d’analyse conserve une seule voie par accident :

1. une voie ayant le statut `matched` est préférée ;
2. parmi les voies appariées, la plus petite distance est retenue ;
3. `osm_way_id` et `osm_source` servent à départager les ex æquo de manière stable.

La sortie finale reste donc unique par `Num_Acc`.

## Extraction des routes OSM

### Source et outil

Les voies sont extraites depuis des fichiers `.osm.pbf` au moyen de `pyrosm`, en mode `out_of_core`, avec conservation d’un sous-ensemble d’attributs utiles à l’analyse.

### Classes de voie conservées

L’extraction filtre les voies selon les classes OSM suivantes :

```text
motorway
trunk
primary
secondary
tertiary
unclassified
residential
living_street
service
track
cycleway
```

La classe OSM est conservée dans le champ `highway`.

La distinction fonctionnelle est importante :

- `tertiary` correspond à des liaisons locales entre petites localités ou centres locaux ;
- `unclassified` correspond à des routes publiques non résidentielles de desserte locale, généralement de moindre importance que `tertiary`.

Dans l’analyse rurale, `unclassified` est la classe qui correspond le mieux aux petites routes locales de desserte. Elle ne doit pas être confondue avec une voie non classée ou non appariée.

### Attributs OSM conservés

Les sorties routières conservent notamment :

```text
highway
name
ref
maxspeed
lanes
width
surface
smoothness
lit
oneway
access
bicycle
motor_vehicle
cycleway
cycleway:left
cycleway:right
sidewalk
shoulder
```

La voie OSM reçoit l’identifiant `osm_way_id`, issu du champ OSM `id`.

Les routes extraites sont enregistrées dans un GeoParquet, par défaut :

```text
osm/derived/roads.geoparquet
```

## Construction des routes candidates

### Entrées

Le script de recherche de candidats prend :

- un GeoParquet BAAC d’accidents ;
- un GeoParquet OSM de voies ;
- un chemin de sortie ;
- un rayon de recherche, fixé par défaut à 30 mètres ;
- un SCR métrique, EPSG:2154 par défaut pour la France métropolitaine.

### Préparation géographique

Les accidents et les routes sont :

1. filtrés pour supprimer les géométries nulles ou vides ;
2. reprojetés dans le SCR métrique choisi ;
3. limités à l’emprise du fichier routier, élargie du rayon de recherche.

Cette dernière étape évite de rechercher des candidats pour des accidents situés hors de l’extrait OSM régional analysé.

### Recherche spatiale

Pour chaque accident dans l’emprise, une jointure spatiale `dwithin` identifie les routes situées à une distance inférieure ou égale au rayon de recherche.

La distance exacte point–ligne est ensuite calculée pour chaque candidat :

```text
distance_m = distance entre le point d’accident et la géométrie de la voie OSM
```

Chaque ligne reçoit :

- `Num_Acc` ;
- `osm_way_id` ;
- `highway` ;
- `distance_m` ;
- `match_status` ;
- `osm_source`.

Les statuts sont :

- `matched` : au moins une voie candidate a été trouvée ;
- `unmatched` : aucune voie candidate dans l’extrait routier et le rayon considérés.

### Sélection locale de la voie candidate

Dans la sortie de chaque extrait OSM, une seule route est retenue par accident :

1. distance minimale ;
2. `osm_way_id` comme règle de départage stable en cas d’égalité.

Le résultat est reprojeté en EPSG:4326 et enregistré en GeoParquet.

### Limites de l’appariement

L’appariement est bidimensionnel :

- il associe l’accident à la voie la plus proche géométriquement ;
- il ne connaît pas l’altitude ni les niveaux de circulation ;
- il peut être ambigu sous un pont, une bretelle, une voie ferrée ou une infrastructure superposée ;
- une route non appariée dans un extrait régional n’est pas nécessairement absente d’OSM : elle peut être couverte par un autre extrait ou se trouver hors emprise.

L’appariement mesure donc la proximité géométrique à une voie OSM, pas une certitude absolue sur la voie réellement empruntée.

## Audit des appariements

L’audit global compare les accidents BAAC géolocalisés à tous les fichiers régionaux de candidats disponibles pour une année.

Il distingue :

- les accidents appariés dans au moins une région ;
- les accidents évalués mais sans candidat dans aucune région ;
- les accidents absents de tous les fichiers de candidats ;
- les accidents globalement non appariés.

L’audit produit également un statut détaillé par accident et une synthèse par département. Il permet de distinguer un défaut d’appariement d’une absence d’évaluation liée à l’emprise ou au découpage régional des fichiers OSM.

## Hypothèses de travail

Les propositions ci-dessous sont des hypothèses à explorer. Leur présence dans ce document ne signifie pas que les données les confirment. Les identifiants H0 à H4 sont conservés tels que définis pour ce projet ; ils ne désignent pas ici une hypothèse nulle et quatre hypothèses alternatives au sens d'un test statistique.

### H0 — Type de voie

Le type de voie est corrélé à la dangerosité des accidents impliquant des EDPM et des vélos.

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

## Limites générales

- Le BAAC recense des accidents corporels, pas l’ensemble des chutes ou incidents.
- Les analyses actuelles ne disposent pas de dénominateur d’exposition : pas de kilomètres, heures, trajets ou parts modales comparables par mode et par type de voie.
- Les associations observées concernent donc la gravité parmi les accidents corporels recensés, non un risque absolu de circulation.
- La qualité de l’appariement OSM dépend de la géolocalisation BAAC, de l’exhaustivité d’OSM, du rayon choisi, de l’emprise régionale et de l’ambiguïté géométrique entre voies proches ou superposées.
- Les accidents hors agglomération impliquant des EDPM sont peu nombreux dans plusieurs sous-groupes : les proportions doivent toujours être accompagnées du nombre d’accidents et du nombre d’événements.
- Les données BAAC sont brutes et les informations concernant certains usagers, notamment des conducteurs en fuite, peuvent être incomplètes.