# Méthodologie de production des données et hypothèses de travail

## Objet et périmètre

Ce projet construit une table d’analyse des accidents corporels à partir des données BAAC annuelles, des voies OpenStreetMap extraites de fichiers PBF et d’un appariement géographique entre un accident et une voie OSM. La production couvre 2019 à 2024 et conserve une ligne par accident. Les comparaisons de gravité portent sur les accidents corporels recensés, et non sur un risque par trajet ou par kilomètre.

## Sources et conversion BAAC

Les quatre tables principales sont `caracteristiques`, `lieux`, `vehicules` et `usagers`. `Num_Acc` permet de les relier ; `(Num_Acc, id_vehicule)` rattache les occupants à un véhicule. Un piéton rattaché au véhicule qui l’a heurté n’est pas compté parmi les usagers de son mode. Le fichier annexe `vehicules-immat` n’entre pas dans le calcul analytique.

`src/convert_baac_to_parquet.py` lit les CSV avec Polars. `Num_Acc` est imposé en chaîne dans les quatre tables et `id_vehicule` en chaîne dans `vehicules` et `usagers`, afin d’éviter des conversions flottantes incompatibles entre millésimes. Les lignes intégralement vides sont retirées explicitement ; les clés manquantes dans des lignes non vides sont signalées. L’ingestion vérifie les identifiants, leur unicité dans les caractéristiques et les relations entre caractéristiques, véhicules et usagers non piétons avant de produire les Parquet annuels.

Le script crée aussi `BAAC/derived/<année>/caract-<année>.geoparquet` à partir des coordonnées valides en EPSG:4326. Les coordonnées sans valeur utilisable ne deviennent pas des points fictifs.

## Routes OSM et appariement

`src/extract_osm_roads.py` utilise Pyrosm pour lire les PBF, ne conserve que les classes de voie prévues (`motorway`, `trunk`, `primary`, `secondary`, `tertiary`, `unclassified`, `residential`, `living_street`, `service`, `track`, `cycleway`) et produit des GeoParquet routiers avec la classe `highway`, `osm_way_id` et les attributs disponibles sur la voie.

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

## Hypothèses de travail

Les propositions qui suivent sont des hypothèses à explorer, non des conclusions. H0 à H4 sont les identifiants de travail du projet, sans acception formelle d’hypothèse nulle ou alternative.

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

### H5 — Accès réglementé et conflits de circulation

EDPM hors agglomération : repérer les accidents appariés à une route OSM autre qu’une piste cyclable, puis vérifier si la circulation des EDPM y était effectivement interdite. La règle générale les limite hors agglomération aux voies vertes et pistes cyclables, mais une autorité compétente peut autoriser certaines routes limitées à 80 km/h ou moins par décision motivée. unclassified seul ne suffit donc pas à conclure « infraction ».

VL et cyclomoteurs près des voies cyclables : compter les accidents impliquant ces véhicules appariés à cycleway, puis examiner leur position et les règles d’accès locales. Un accident impliquant un VL peut avoir eu lieu à une traversée, sans que le VL ait circulé sur la piste. De même, certains cyclomoteurs peuvent être autorisés sur piste par décision locale.

### H6 — Position relative de la dangerosité des EDPM

la dangerosité attribuée aux EDPM est mal estimée relativement à celle des vélos sans assistance, des VAE et des cyclomoteurs. Le sens et l'ampleur de l'écart sont déterminés par les comparaisons, et non fixés dans l'hypothèse.

## Limites générales

- Le BAAC recense des accidents corporels et non tous les incidents ou chutes.
- La table ne contient pas de dénominateur d’exposition par mode et par voie ; les parts de gravité conditionnelles aux accidents ne sont pas des risques par déplacement.
- Le rattachement OSM dépend de la géolocalisation BAAC, des données OSM, de l’emprise des extraits régionaux et de la distance point–voie, sans information altimétrique.
- Les petits effectifs, particulièrement pour les EDPM hors agglomération, exigent l’affichage des nombres bruts avec les proportions.
- Les données BAAC peuvent comporter des gravités inconnues et des usagers non rattachés ; ces cas doivent rester visibles dans les contrôles de qualité.
