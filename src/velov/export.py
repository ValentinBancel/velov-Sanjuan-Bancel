"""Livraison : sortir du registre la version qui porte un alias, sous la forme que l'image de l'API attend.

L'alias est résolu ICI, avant le build : l'image contient le modèle, pas de dépendance à MLflow, et l'API ne
dépend pas de la disponibilité du registre (voir docs/adr/0003-livraison-du-modele.md). Le paquet est vérifié
par son empreinte SHA-256, puis la version du registre est inscrite dans metadata.json : GET /v1/model la
renvoie, c'est ce qui rend le modèle servi retrouvable jusqu'à son run (EX-13).

Usage :
    python -m velov.export --alias champion --out models/
    python -m velov.export --alias previous-champion --out models/    # retour arrière
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from velov.registry import RegistryError, fetch
from velov.tracking import MODEL_NAME
from velov.train import METADATA_FILENAME


def export(alias: str, out_dir: Path) -> dict:
    """Écrit model.joblib + metadata.json de la version `@alias` dans `out_dir`. Renvoie le bloc `registry`."""
    with tempfile.TemporaryDirectory() as tmp:
        package = fetch(alias, Path(tmp))
        metadata = dict(package.metadata)
        metadata["registry"] = {
            "name": MODEL_NAME,
            "version": package.version,
            "alias": alias,
            "run_id": package.run_id,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package.directory / metadata["artifact"]["file"], out_dir / metadata["artifact"]["file"])
        (out_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    return metadata["registry"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporte une version du registre vers models/.")
    parser.add_argument("--alias", default="champion", help="champion (défaut), previous-champion, challenger")
    parser.add_argument("--out", type=Path, default=Path("models"))
    args = parser.parse_args()
    try:
        registry = export(args.alias, args.out)
    except RegistryError as exc:
        sys.exit(f"Export impossible : {exc}")
    print(f"{registry['name']} v{registry['version']} (@{args.alias}, run {registry['run_id'][:8]})", end=" ")
    print(f"exporté dans {args.out}")


if __name__ == "__main__":
    main()
