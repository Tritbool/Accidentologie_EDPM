# Accidentologie EDPM — BAAC × OpenStreetMap × ONISR

Analyse ouverte de l'accidentalité des vélos, VAE, EDPM et cyclomoteurs en
France (métropole et DROM-COM), de 2019 à 2024 : appariement de l'intégralité
du BAAC géolocalisé aux classes de voies OpenStreetMap (couverture > 99 %),
croisement avec les bilans annuels de l'ONISR, et examen de dix hypothèses
(H0–H9) allant de la description à la recommandation d'aménagement.

## Motivation

La catégorie réglementaire « hors agglomération » est trop grossière pour
décrire le risque routier des mobilités légères. Ce projet teste si la classe
fonctionnelle de la voie (hiérarchie OSM, VMA, fréquentation) décrit mieux
la gravité des accidents corporels, et en dérive des réponses explicites —
chacune assortie de ses limites — sur des questions d'actualité réglementaire :
dangerosité relative des EDPM, usage rural hors cadre légal, ouverture
encadrée des voies peu fréquentées (H8), pertinence du plafond uniforme
de 25 km/h au regard de l'hétérogénéité mécanique des engins (H9).

## Périmètre

- **BAAC 2019–2024**, France métropolitaine + DROM-COM (Saint-Barthélemy et
  Saint-Martin via l'extrait OSM Amérique centrale, Saint-Pierre-et-Miquelon
  via l'extrait Canada)
- **Cohortes** : EDPM (`catv = 50`), vélo sans assistance (`01`), VAE (`80`),
  cyclomoteur (`02`), EDP sans moteur (`60`)
- **Appariement OSM** : 11 classes `highway`, un extrait régional par année
  (Geofabrik), rayon 30 m en projection métrique EPSG:2154
- **Sources complémentaires** : bilans annuels ONISR 2019–2025, INSEE
  (prix du gazole), Cerema EMC² (mobilités), littérature technique (H9)
- **Analyses** : en agglomération et hors agglomération ; les comparaisons
  de gravité portent sur les accidents corporels recensés, jamais sur un
  risque par trajet ou par kilomètre

## Structure du dépôt

- `src/` — conversion BAAC, extraction OSM, appariement, construction de
  la table d'analyse, analyses H0–H9
- `outputs/` — tables d'analyse, CSV agrégés, figures
- `docs/` — [méthodologie détaillée](docs/METHODOLOGIE.md) et document
  d'étude (H0–H9 avec réponses, annexes, intervalles de Wilson)
- Les parquets BAAC bruts ne sont pas versionnés : les régénérer depuis
  [data.gouv.fr](https://www.data.gouv.fr/) (Licence Ouverte)

## Reproduire

```bash
uv run python src/convert_baac_to_parquet.py
uv run python src/extract_osm_roads.py
uv run python src/bind_accident_road_candidates.py
uv run python src/audit_global_matches.py
uv run python src/build_accident_table.py
# analyses H0–H9 : voir docs/METHODOLOGIE.md