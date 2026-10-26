"""Ingestion bronze : flux GBFS Vélo'v de la Métropole de Lyon -> fichiers JSON bruts.

Bronze = la réponse de la source, telle que reçue, jamais modifiée. Ni typage, ni dédoublonnage,
ni nettoyage ici : c'est le rôle de silver (dbt). Si la logique silver change, on rejoue silver
sur le bronze, sans avoir à re-télécharger une source qui, elle, ne se rejoue pas.

Disposition sur disque :
    data/bronze/<flux>/dt=AAAA-MM-JJ/<last_updated>.json

Idempotence (EX-10) : la clé est `payload["last_updated"]`, une propriété de la DONNÉE (jamais now()).
Relancer l'ingestion pendant que la source n'a pas changé retombe sur le même nom de fichier :
rien n'est écrit. La date de partition est la date UTC de `last_updated`, pas celle du run.
Un fichier existant n'est jamais écrasé. L'écriture est atomique (fichier .tmp puis replace) :
un crash ne laisse jamais un JSON tronqué sous le nom définitif.

Usage :
    python -m velov.ingest [--bronze data/bronze]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("velov.ingest")

GBFS_INDEX_URL = "https://download.data.grandlyon.com/files/rdata/jcd_jcdecaux.jcdvelov/gbfs.json"
GBFS_VERSION = "2.3"  # version publiée par la Métropole (gbfs_versions.json propose aussi la 3.0 ailleurs)
FEEDS = ("station_status", "station_information")
TIMEOUT_S = 10
DEFAULT_BRONZE = Path("data/bronze")


class IngestError(RuntimeError):
    """Source injoignable ou réponse inexploitable."""


@dataclass
class IngestReport:
    written: int = 0
    already_present: int = 0


def fetch_bytes(url: str, timeout: float = TIMEOUT_S) -> bytes:
    """GET `url` et renvoie les octets reçus (injectable dans les tests)."""
    if not url.startswith("https://"):
        raise IngestError(f"URL non HTTPS refusée : {url}")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310  # schéma https vérifié ci-dessus
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IngestError(f"Échec de la requête vers {url} : {exc}") from exc


def _parse(raw: bytes, url: str) -> dict:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise IngestError(f"Réponse non JSON depuis {url} : {exc}") from exc
    if not isinstance(payload, dict):
        raise IngestError(f"Réponse inattendue depuis {url} : objet JSON attendu")
    return payload


def feed_urls(index: dict, version: str = GBFS_VERSION) -> dict[str, str]:
    """Extrait l'URL de chaque flux voulu de gbfs.json ; refuse une autre version GBFS que celle épinglée."""
    if index.get("version") != version:
        raise IngestError(f"Version GBFS {index.get('version')!r} publiée, {version!r} attendue : à revoir")
    try:
        feeds = next(iter(index["data"].values()))["feeds"]
        urls = {f["name"]: f["url"] for f in feeds}
        return {name: urls[name] for name in FEEDS}
    except (KeyError, StopIteration, AttributeError, TypeError) as exc:
        raise IngestError(f"Index GBFS inexploitable (flux manquant ou structure inconnue) : {exc!r}") from exc


def bronze_path(bronze: Path, feed: str, last_updated: int) -> Path:
    day = datetime.fromtimestamp(last_updated, tz=UTC).strftime("%Y-%m-%d")
    return bronze / feed / f"dt={day}" / f"{last_updated}.json"


def write_once(path: Path, content: bytes) -> bool:
    """Écrit `content` sous `path` si le fichier n'existe pas. Renvoie True si écrit, False si déjà présent."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)  # atomique sur un même système de fichiers
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return True


def ingest(bronze: Path = DEFAULT_BRONZE, index_url: str = GBFS_INDEX_URL, fetch=fetch_bytes) -> IngestReport:
    """Récupère chaque flux et le stocke tel quel. Toute erreur réseau est levée avant la moindre écriture
    du flux concerné ; les flux déjà écrits restent valides (chacun est autonome).

    Raises:
        IngestError: réseau, JSON invalide, version GBFS inattendue ou `last_updated` absent.
    """
    urls = feed_urls(_parse(fetch(index_url), index_url))
    report = IngestReport()
    for feed, url in urls.items():
        raw = fetch(url)
        last_updated = _parse(raw, url).get("last_updated")
        if not isinstance(last_updated, int) or isinstance(last_updated, bool):
            raise IngestError(f"`last_updated` absent ou invalide dans {url}")
        path = bronze_path(bronze, feed, last_updated)
        if write_once(path, raw):
            report.written += 1
            logger.info("écrit %s", path)
        else:
            report.already_present += 1
            logger.info("déjà présent %s", path)
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingère les flux GBFS Vélo'v dans la couche bronze (idempotent).")
    parser.add_argument("--bronze", type=Path, default=DEFAULT_BRONZE)
    args = parser.parse_args()
    try:
        report = ingest(args.bronze)
    except IngestError as exc:
        logger.error("Ingestion interrompue : %s", exc)
        sys.exit(1)
    print(f"{report.written} fichiers écrits, {report.already_present} déjà présents")


if __name__ == "__main__":
    main()
