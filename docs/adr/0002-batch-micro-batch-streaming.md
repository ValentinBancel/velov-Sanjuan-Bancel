# ADR-0002 : Batch, micro-batch ou streaming pour la collecte et la préparation des données

- Statut : accepté
- Date : 2026-10-29
- Auteurs : binôme Sanjuan / Bancel

## Contexte
Le besoin : prédire à chaque heure H le nombre de vélos disponibles à H+1 par station, avec une API qui reçoit l'état
courant de la station. Les données viennent du flux GBFS de la Métropole de Lyon (instantanés `station_status`,
mis à jour à intervalle court, de l’ordre de la minute (à confirmer dans le champ `ttl` du flux) ; la source ne conserve pas l'historique, donc **on doit collecter nous-mêmes**).
L'entraînement a lieu une fois par session ou par semaine ; aucune décision n'est prise à la seconde.
Contraintes : une VM unique, budget quasi nul, équipe de deux, et l'exigence de pouvoir rejouer le pipeline (EX-10).

Ordres de grandeur (estimations à vérifier sur la collecte réelle, pas des mesures) : ~400 stations x ~150 octets par
instantané, soit ~60 Ko par fichier ; un instantané toutes les 5 min = 288 fichiers/jour, ~17 Mo/jour, ~0,5 Go/mois.

## Options envisagées
| Option | Avantages | Inconvénients | Coût estimé |
|---|---|---|---|
| A. Batch quotidien (un appel par jour) | Le plus simple | Un seul instantané par jour : inutilisable pour une cible horaire, et la source n'a pas d'historique à rattraper | ~0 |
| B. Micro-batch : ingestion toutes les 5 min (cron), silver/gold recalculés par `dbt build` | Fichiers bruts rejouables, idempotent (clé = `last_updated`), un instantané tous les 5 min suffit pour « dernier état avant H », simple à exploiter | Résolution limitée à 5 min, reconstruction complète de silver/gold à chaque build (négligeable à cette taille) | VM existante (~5-15 EUR/mois), stockage ~0,5 Go/mois soit quelques centimes |
| C. Streaming (Redpanda/Kafka + consommateur continu) | Latence de l'ordre de la seconde, découplage producteurs/consommateurs, utile si plusieurs consommateurs temps réel | Un broker à exploiter et surveiller, ~1-2 Go de RAM en plus sur la VM, ou 100-400 EUR/mois managé ; la source ne se met à jour que par intervalles, donc le « flux » est un polling déguisé | +10-30 EUR/mois (VM plus grosse) à 100-400 EUR/mois (managé), plus du temps d'exploitation |

## Décision
Nous retenons **B : micro-batch**. Le besoin est horaire, donc la fraîcheur de 5 minutes suffit largement ; la
contrainte forte est la rejouabilité, et des fichiers JSON bruts immuables plus un `dbt build` sans état la donnent
gratuitement. Le streaming n'apporterait aucun gain de précision pour une prédiction à une heure et ajouterait un
composant à exploiter ; on garde la porte ouverte (compose, stretch du TP3) sans en dépendre.

## Conséquences
- La collecte doit tourner en continu sur la VM : tout intervalle non collecté est définitivement perdu (voir runbook).
- La fraîcheur maximale de gold est celle du dernier `dbt build` ; pour l'entraînement c'est sans importance. Pour de
  l'inférence temps réel, c'est l'API qui reçoit l'état courant de la requête, pas gold.
- Le jour où la décision doit être revue : plusieurs consommateurs temps réel, alerte sur événement de station
  (moins de 1 min), ou volume qui rend la reconstruction complète trop lente (dizaines de Go). Surveiller la durée
  d'un `dbt build` et la taille de `data/bronze`.
- Les coûts ci-dessus sont des ordres de grandeur à confirmer avec la facture et la taille réelle de `data/bronze` après une semaine.
