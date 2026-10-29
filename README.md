# velov-mlops

Projet fil rouge du cours **Industrialisation de l'IA dans le Cloud** (M2 Data Engineering / IA).

Objectif métier : prédire, pour chaque station Vélo'v, le nombre de vélos disponibles **dans une heure**,
et servir cette prédiction via une API fiable, conteneurisée, puis déployée (on-premise et cloud).

## Progression par session

Chaque étape du cours correspond à un tag git. Absent ou bloqué : repartez du tag de fin de la session précédente.

| Tag | Contenu |
|---|---|
| `s1-start` | Données simulées, features, entraînement. API à compléter (TP1) |
| `s1-end` | Modèle v0 servi par FastAPI, tests pytest |
| `tp3` | Pipeline de données : bronze idempotent, silver/gold dbt sous contrat, entraînement depuis gold |

```bash
git checkout s1-end          # récupérer l'état de fin de S1
git checkout -b mon-binome   # travailler sur sa propre branche
```

## Démarrage rapide

Prérequis : Python 3.11+ (3.12 recommandé), git. Docker sera nécessaire en S2.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .                     # rend le package velov importable

python -m velov.data                 # génère data/velov_history.csv (simulateur, seed fixe)
python -m velov.train                # entraîne et écrit models/model.joblib + models/metadata.json
python -m velov.train --mlflow       # idem + journalisation MLflow (puis : mlflow ui)
pytest                               # tests
uvicorn velov.api.main:app --reload  # API sur http://127.0.0.1:8000/docs (--reload : en dev uniquement)
```

Exemple d'appel :

```bash
curl -X POST http://127.0.0.1:8000/v1/predict \
  -H "Content-Type: application/json" \
  -d '{"station_id": 3, "timestamp": "2026-10-06T08:00:00+02:00", "capacity": 20,
       "bikes_available": 12, "temperature": 14.5, "is_raining": false}'
```

Le `timestamp` doit porter un fuseau (`+02:00`, `Z`...) : sans fuseau, l'API répond 422.
Les instants sont renvoyés et journalisés en UTC (`"target_timestamp": "2026-10-06T07:00:00Z"`).

## Avec Docker (S2)

```bash
cp .env.example .env                 # puis choisir un vrai mot de passe dans .env
python -m velov.data && python -m velov.train   # l'image embarque models/ : entraîner d'abord
docker compose up --build -d         # API + PostgreSQL
docker compose ps                    # api et db doivent être "healthy"
curl http://127.0.0.1:8001/ready     # port réglable avec API_PORT dans .env
./scripts/build.sh                   # image taguée <version du modèle>-<commit>
```

Sans `POSTGRES_PASSWORD` dans `.env`, `docker compose up` refuse de démarrer (aucun mot de passe par défaut, EX-06).

## Pipeline de données (S3)

```
GBFS Vélo'v ──► bronze (JSON brut) ──► silver (dbt, typé, dédoublonné) ──► gold (dbt, contrat) ──► train.py (Pandera)
 data.grandlyon   data/bronze/<flux>/    silver_station_status/information     gold_velov_hourly       models/
                  dt=AAAA-MM-JJ/*.json   silver_weather, silver_station_hourly  (data/velov.duckdb)
```

| Couche | Rôle | Où | Rejouable |
|---|---|---|---|
| bronze | Réponse de la source telle que reçue, jamais modifiée | `data/bronze/<flux>/dt=<date UTC>/<last_updated>.json` | écriture unique : un fichier existant n'est jamais écrasé |
| silver | Typage, dépliage, dédoublonnage. Aucune règle métier | modèles `silver_*` (dbt) | `dbt build` reconstruit tout depuis le bronze |
| gold | Table du contrat : une ligne par station et par heure UTC | `gold.gold_velov_hourly` (dbt, contrat `enforced`) | idem |

```bash
# 1. Bronze (une des deux) : collecte réelle, ou rejeu de 48 h simulées pour développer sans attendre
python -m velov.ingest                 # idempotent : relancer sans nouvelle donnée écrit 0 fichier
python -m velov.gbfs_replay            # bronze de démonstration (météo : format provisoire)

# 2. Silver + gold + tests (depuis la racine du dépôt)
dbt build --project-dir velov_dbt --profiles-dir velov_dbt

# 3. Entraîner depuis gold (le contrat Pandera est vérifié avant d'entraîner)
python -m velov.train                  # --test-days N pour réduire la fenêtre de test si l'historique est court
```

Depuis un clone propre, `data/` est vide (ignoré par git) : sans bronze, `dbt build` s'arrête sur
`No files found that match the pattern "data/bronze/..."`. Faire l'étape 1 d'abord.

**Choix de conception**

- *Idempotence (EX-10)* : le nom d'un fichier bronze vient de la donnée (`last_updated`), jamais de l'horloge du run.
  Gold n'utilise ni `now()` ni état : le reconstruire donne la même table.
- *Pas de fuite temporelle (EX-11)* : pour l'heure H, gold prend le dernier instantané pris **au plus tard à H**
  (et au plus `max_lag_minutes` = 15 min avant). Pas de moyenne sur l'heure (elle inclurait le futur de H).
  La capacité vient d'un `asof join` sur l'instantané, jamais d'une valeur plus récente.
- *Contrat (EX-12)* : trois filets. (1) `contract: enforced` dans `gold/schema.yml` : types et non-nuls vérifiés par dbt
  à la création, l'ancienne table reste en place si ça échoue. (2) Tests dbt : `not_null`, `relationships`
  (station connue), unicité (station, heure), bornes de capacité, et `gold_keeps_enough_rows` (seuil
  `max_dropped_ratio` = 5 % : une jointure qui fait perdre trop de lignes fait échouer le build). (3) `train.py`
  revalide avec Pandera (`src/velov/contract.py`) et refuse d'entraîner sinon.
- Limite assumée de (2) : dbt écrit gold avant d'exécuter ses tests, la table est donc « publiée » un court instant
  même si un test échoue ensuite ; ce qui protège le modèle, c'est le contrôle Pandera à l'entraînement.

Tests : `pytest tests/test_pipeline.py` (mini-bronze jetable, base DuckDB jetable, ne touche pas à `data/`).

## Exigences du projet

Le service doit respecter les exigences de [docs/exigences.md](docs/exigences.md), de S1 à S9.
Chaque rendu indique celles qu'il couvre et comment le vérifier.


## Structure

```
src/velov/
  data.py          simulateur de données (remplacé par l'open data en S3, même schéma)
  features.py      features partagées entraînement / API (anti training-serving skew)
  ingest.py        ingestion bronze des flux GBFS (idempotente, atomique)
  gbfs_replay.py   bronze de démonstration depuis le simulateur
  contract.py      data contract de la table gold (Pandera)
  train.py         entraînement depuis gold (ou CSV), baseline, artefact + metadata.json, option MLflow
  api/main.py      FastAPI : /health, /ready, /v1/model, /v1/predict, /v1/predict/batch
  api/schemas.py   contrat d'entrée / sortie (Pydantic)
velov_dbt/         projet dbt : macros de lecture du bronze, modèles silver et gold, tests
tests/             pytest (features, API, ingestion, contrat, pipeline dbt)
docs/              templates : model card, runbook, ADR
exercices/         démo pickle (S1)
```

## Usage de l'IA générative

Chaque activité indique son mode : **sans IA**, **IA déclarée** ou **IA imposée**.
En mode IA déclarée, ajoutez dans la description de vos commits ou de votre rendu :
outil utilisé, ce que vous lui avez demandé, ce que vous avez vérifié ou corrigé.
