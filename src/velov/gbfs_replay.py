"""Bronze de démonstration au format GBFS 2.3, généré depuis le simulateur (`velov.data.simulate`).

Pourquoi : la vraie collecte ne remplit le bronze qu'au rythme du temps qui passe. Pour développer
et tester silver/gold (dbt) tout de suite, on rejoue 48 h d'historique simulé sous la forme exacte
que prendrait la collecte :

    data/bronze/station_status/dt=AAAA-MM-JJ/<last_updated>.json
    data/bronze/station_information/dt=AAAA-MM-JJ/<last_updated>.json
    data/bronze/weather/dt=AAAA-MM-JJ/<heure>.json

Mêmes chemins, même nommage, même écriture atomique et jamais d'écrasement que `velov.ingest`
(on en réutilise les fonctions) : le rejouer est idempotent (relancer = 0 fichier écrit).

Choix de simulation : le simulateur est horaire ; entre deux heures, la valeur de l'heure est maintenue
(pas d'interpolation, pour ne pas inventer de dynamique). `last_reported` précède légèrement l'instantané
(décalage déterministe par station), comme dans le vrai flux.

ATTENTION, format provisoire pour la météo : en attendant la confirmation de la vraie source météo par
l'enseignant, `weather/` utilise un format maison minimal (`last_updated`, `source`, `data.temperature`,
`data.is_raining`). À remplacer par la réponse brute de la vraie source dès qu'elle est connue ;
GBFS, lui, ne porte pas de météo.

Usage :
    python -m velov.gbfs_replay [--hours 48] [--stations 20] [--bronze data/bronze]
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

from velov.data import simulate
from velov.ingest import DEFAULT_BRONZE, GBFS_VERSION, IngestReport, bronze_path, write_once

SNAPSHOT_STEP_S = 300  # un instantané toutes les 5 minutes
HOUR_S = 3600
TTL_STATUS_S = 1  # valeurs du vrai flux (station_status: 1 s, station_information: 300 s)
TTL_INFORMATION_S = 300


def _dump(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=False).encode()


def _envelope(last_updated: int, ttl: int, data: dict) -> dict:
    return {"last_updated": last_updated, "ttl": ttl, "version": GBFS_VERSION, "data": data}


def replay(hours: int = 48, stations: int = 20, bronze: Path = DEFAULT_BRONZE) -> IngestReport:
    """Écrit le bronze de démonstration. Raises: ValueError si `hours` < 1 (stations: voir `simulate`)."""
    if hours < 1:
        raise ValueError("hours doit être >= 1")
    history = simulate(days=max(2, math.ceil(hours / 24)), n_stations=stations)
    first_hour = history["timestamp"].min()
    history = history[history["timestamp"] < first_hour + pd.Timedelta(hours=hours)]
    t0 = int(first_hour.timestamp())

    # (heure epoch, station_id) -> ligne ; capacité et météo ne dépendent pas de l'heure / de la station.
    by_hour = {
        (int(ts.timestamp()), int(sid)): (int(cap), int(bikes))
        for ts, sid, cap, bikes in zip(
            history["timestamp"], history["station_id"], history["capacity"], history["bikes_available"], strict=True
        )
    }
    capacities = {sid: cap for (_, sid), (cap, _) in by_hour.items()}
    weather = history.drop_duplicates("timestamp").set_index("timestamp")

    report = IngestReport()

    def put(feed: str, last_updated: int, payload: dict) -> None:
        if write_once(bronze_path(bronze, feed, last_updated), _dump(payload)):
            report.written += 1
        else:
            report.already_present += 1

    for hour_start in range(t0, t0 + hours * HOUR_S, HOUR_S):
        row = weather.loc[pd.Timestamp(hour_start, unit="s", tz="UTC")]
        put(
            "weather",
            hour_start,
            {
                "last_updated": hour_start,
                "source": "simulated",
                "data": {"temperature": float(row["temperature"]), "is_raining": bool(row["is_raining"])},
            },
        )
        for last_updated in range(hour_start, hour_start + HOUR_S, SNAPSHOT_STEP_S):
            status = []
            for sid, capacity in capacities.items():
                bikes = by_hour[(hour_start, sid)][1]
                status.append(
                    {
                        "station_id": str(sid),  # texte, comme dans le vrai flux
                        "num_bikes_available": bikes,
                        "num_docks_available": capacity - bikes,
                        "is_installed": True,
                        "is_renting": True,
                        "is_returning": True,
                        "last_reported": last_updated - (sid * 37) % SNAPSHOT_STEP_S,
                    }
                )
            put("station_status", last_updated, _envelope(last_updated, TTL_STATUS_S, {"stations": status}))
            info = [
                {
                    "station_id": str(sid),
                    "name": f"STATION {sid}",
                    "lat": round(45.74 + 0.002 * sid, 6),
                    "lon": round(4.83 + 0.002 * sid, 6),
                    "capacity": capacity,
                }
                for sid, capacity in capacities.items()
            ]
            put("station_information", last_updated, _envelope(last_updated, TTL_INFORMATION_S, {"stations": info}))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Génère un bronze GBFS de démonstration (idempotent).")
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--stations", type=int, default=20)
    parser.add_argument("--bronze", type=Path, default=DEFAULT_BRONZE)
    args = parser.parse_args()
    report = replay(args.hours, args.stations, args.bronze)
    print(f"{report.written} fichiers écrits, {report.already_present} déjà présents")


if __name__ == "__main__":
    main()
