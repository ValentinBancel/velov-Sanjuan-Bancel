"""Data contract de la table gold (une ligne par station et par heure UTC), vérifié avec Pandera.

C'est le même contrat que `velov_dbt/models/gold/schema.yml` (types, non-nuls) auquel on ajoute les règles
métier que dbt vérifie par tests. Le doublon est volontaire : dbt protège la publication de la table (EX-12),
Pandera protège l'entraînement, y compris si la table a été produite ou modifiée hors du pipeline.

Usage :
    validate_gold(frame)   # renvoie le DataFrame, ou lève ContractError avec TOUTES les violations
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa

MAX_ABS_TEMPERATURE_C = 60.0  # au-delà, c'est une valeur aberrante (capteur, unité), pas la météo de Lyon


class ContractError(ValueError):
    """La table ne respecte pas le data contract : on n'entraîne pas dessus (EX-12)."""


GOLD_SCHEMA = pa.DataFrameSchema(
    {
        "station_id": pa.Column(int, pa.Check.ge(0), nullable=False),
        "timestamp": pa.Column(
            pd.DatetimeTZDtype(tz="UTC"),
            pa.Check(lambda s: (s.dt.minute == 0) & (s.dt.second == 0), error="heure ronde attendue"),
            nullable=False,
            coerce=True,  # DuckDB rend le fuseau de la session : on ramène en UTC avant de contrôler
        ),
        "capacity": pa.Column(int, pa.Check.gt(0), nullable=False),
        "bikes_available": pa.Column(int, pa.Check.ge(0), nullable=False),
        "temperature": pa.Column(
            float, pa.Check.in_range(-MAX_ABS_TEMPERATURE_C, MAX_ABS_TEMPERATURE_C), nullable=False
        ),
        "is_raining": pa.Column(bool, nullable=False),
    },
    checks=[
        pa.Check(lambda df: df["bikes_available"] <= df["capacity"], error="bikes_available <= capacity"),
        # `unique=[...]` de Pandera plante au formatage du rapport avec pandas 3 : check équivalent explicite.
        pa.Check(
            lambda df: ~df.duplicated(["station_id", "timestamp"], keep=False), error="(station_id, timestamp) unique"
        ),
    ],
    strict=True,  # une colonne en trop ou en moins est une violation
    ordered=False,
)


def validate_gold(df: pd.DataFrame) -> pd.DataFrame:
    """Valide `df` contre le contrat. Les violations sont toutes listées (pas seulement la première)."""
    if "timestamp" in df.columns and isinstance(df["timestamp"].dtype, pd.DatetimeTZDtype):
        df = df.assign(timestamp=df["timestamp"].dt.tz_convert("UTC"))
    # La largeur des entiers (int32 DuckDB, int64 CSV) n'est pas une règle métier : on élargit sans perte.
    # Un float reste refusé (aucune conversion silencieuse).
    widened = {c: "int64" for c in df.columns if pd.api.types.is_integer_dtype(df[c].dtype)}
    df = df.astype(widened)
    try:
        return GOLD_SCHEMA.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        # Une règle de ligne produit un cas par colonne et par ligne fautive : on résume par règle.
        cases = exc.failure_cases
        by_rule = cases.groupby(cases["check"].astype(str), dropna=False).agg(
            colonnes=("column", lambda c: ",".join(sorted(c.dropna().astype(str).unique())) or "-"),
            exemples=("failure_case", lambda f: ", ".join(map(str, f.astype(str).unique()[:3]))),
            cas=("check", "size"),
        )
        raise ContractError(
            f"Table gold non conforme au contrat, violations par règle :\n{by_rule.to_string()}"
        ) from exc
