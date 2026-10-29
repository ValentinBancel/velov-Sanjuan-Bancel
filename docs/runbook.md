# Runbook : API velov-availability

> Template à compléter au fil des sessions. Public cible : une personne d'astreinte
> qui ne connaît pas le projet et doit rétablir le service à 3 h du matin.

## 1. Vue d'ensemble
- Ce que fait le service, en deux phrases :
- Schéma d'architecture (lien) :
- Dépendances (base de données, stockage du modèle...) :

## 2. Démarrer / arrêter
```bash
# démarrer
# arrêter
# redémarrer un seul service
```

## 3. Vérifier que tout va bien
| Vérification | Commande | Résultat attendu |
|---|---|---|
| Le process répond | `curl -s http://<hôte>:8000/health` | `{"status":"ok"}` |
| Le modèle est chargé | `curl -s http://<hôte>:8000/ready` | HTTP 200 + version |
| | | |

## 4. Incidents connus
| Symptôme | Cause probable | Diagnostic | Action |
|---|---|---|---|
| `/ready` renvoie 503 | | | |
| Le conteneur redémarre en boucle | | | |
| Latence élevée | | | |

## 5. Déployer une nouvelle version du modèle
1.
2.
3. Retour arrière (rollback) :

## 6. Contacts et escalade
-

## 7. Pipeline de données (collecte, silver, gold)

> Public cible : la personne d'astreinte. Chaque jour non collecté manque à l'entraînement : une collecte arrêtée est un incident.

### Vue d'ensemble
`velov.ingest` (toutes les 5 min) écrit les réponses GBFS brutes dans `data/bronze/<flux>/dt=<date UTC>/<last_updated>.json`.
`dbt build` reconstruit silver puis gold dans `data/velov.duckdb`. `velov.train` lit gold. Rien n'est modifié en place :
tout ce qui est après le bronze se reconstruit.

### Vérifier que la collecte tourne
| Vérification | Commande | Résultat attendu |
|---|---|---|
| Dernier fichier reçu | `ls -t data/bronze/station_status/dt=*/ \| head -1` | un fichier daté de moins de 10 min |
| Ingestion à blanc | `python -m velov.ingest` | `0 fichiers écrits, 2 déjà présents` si la source n'a pas changé, sinon `2 fichiers écrits` |
| Gold à jour | `python -c "import duckdb; print(duckdb.connect('data/velov.duckdb', read_only=True).sql('select max(\"timestamp\") from gold.gold_velov_hourly'))"` | heure ronde récente |
| Pipeline sain | `dbt build --project-dir velov_dbt --profiles-dir velov_dbt` | `PASS=26 WARN=0 ERROR=0` (le nombre exact peut évoluer) |

### Si la collecte s'arrête
1. Lire l'erreur : `python -m velov.ingest` (code retour 1 et message `Ingestion interrompue : ...`).
2. Causes habituelles :
   | Message | Cause probable | Action |
   |---|---|---|
   | `Échec de la requête ... timed out / Name resolution` | réseau de la VM ou source indisponible | tester `curl -I https://download.data.grandlyon.com`, relancer plus tard |
   | `Version GBFS '...' publiée, '2.3' attendue` | la Métropole a changé de version | lire le nouvel index, adapter `GBFS_VERSION` et les macros `velov_dbt/macros/bronze.sql`, ne PAS forcer |
   | `Index GBFS inexploitable` / `last_updated absent` | format de la source modifié | idem : le bronze doit rester la réponse brute, adapter la lecture (silver), pas le fichier |
   | `Permission denied`, `No space left` | droits ou disque de la VM | libérer / corriger, relancer |
3. Relancer le planificateur de la collecte (cron ou service de la VM) et vérifier qu'un nouveau fichier apparaît.
4. **Ce qui n'a pas été collecté est perdu** : l'API ne publie que l'état courant. Noter la période manquante dans le journal d'incident ; elle apparaîtra comme des heures absentes de gold (pas de ligne plutôt qu'une valeur inventée).

### Rejouer une journée
Silver et gold ne gardent pas d'état : rejouer = reconstruire depuis le bronze, sans toucher à la source.
```bash
dbt build --project-dir velov_dbt --profiles-dir velov_dbt          # toute la période du bronze
```
Le résultat est identique à chaque rejeu (EX-10, vérifié par `tests/test_pipeline.py`). Pour rejouer après une correction
de la logique silver/gold : modifier le SQL, relancer la commande, comparer le nombre de lignes avant/après.
Pour re-télécharger une journée : impossible côté source (instantanés seulement). Si le bronze d'un jour est corrompu
(fichier tronqué : ne devrait pas arriver, l'écriture est atomique), supprimer ce seul fichier puis rejouer.

### `dbt build` échoue
| Symptôme | Cause probable | Action |
|---|---|---|
| `No files found that match the pattern "data/bronze/..."` | bronze vide (clone neuf, mauvais dossier) | lancer `python -m velov.ingest` (ou `gbfs_replay`), exécuter dbt depuis la racine du dépôt |
| `Runtime Error ... contract` | gold produit des NULL ou un mauvais type | l'ancienne table gold est conservée ; corriger le SQL puis relancer |
| Test `gold_keeps_enough_rows` en échec | plus de 5 % des heures écartées (météo manquante, capacité inconnue, valeurs impossibles) | regarder `silver_weather` et `silver_station_information` autour de la période ; un trou de collecte météo est la cause la plus probable |
| Test `bikes_within_capacity` en `warn` | quelques valeurs hors bornes dans la source | rien à faire tant que ça reste sous le seuil ; au-delà (error), signaler la source |
| `train` : `Table gold non conforme au contrat` | gold modifié hors pipeline ou dérive de la source | relancer `dbt build`, puis `train` ; ne pas contourner la validation |
