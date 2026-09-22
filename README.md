# Accidentologie EDPM

Analyse ouverte de l’accidentalité des vélos, VAE et EDPM en France,
avec caractérisation du réseau routier par les données BAAC et OpenStreetMap.

## Hypothèse

La catégorie réglementaire « hors agglomération » est trop grossière pour
décrire le risque routier. Les accidents doivent être analysés selon les
caractéristiques fonctionnelles et physiques des voies : rôle dans le réseau,
trafic, vitesse, intersections, géométrie et infrastructures disponibles.

## Périmètre initial

- BAAC : année 2024
- Cohortes : EDPM motorisés (`catv = 50`), vélo (`catv = 01`), VAE (`catv = 80`)
- Localisation : France métropolitaine, puis extension explicite si les données
  et extraits OSM correspondants sont ajoutés
- Analyse principale : accidents hors agglomération (`agg = 1`)

## Limites

- La BAAC recense les accidents corporels connus des forces de l’ordre ;
  elle ne mesure pas les quasi-accidents ni l’exposition.
- `catv = 50` ne distingue pas les monoroues des autres EDPM motorisés.
- Les attributs OSM reflètent la date de l’extrait, pas nécessairement
  l’état exact de la voirie au jour de l’accident.
- Un nombre d’accidents n’est pas un taux de risque sans données de kilomètres
  parcourus par type de voie.

## Données et licences

- BAAC / ONISR : Licence Ouverte
- OpenStreetMap : © les contributeurs d’OpenStreetMap, ODbL
