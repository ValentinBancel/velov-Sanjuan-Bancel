"""Promotion d'un modèle : le challenger devient champion seulement s'il respecte une règle écrite d'avance (EX-14).

    1. Même fenêtre. Challenger et champion sont évalués ICI, sur les `test_days` derniers jours de la table
       actuelle. On ne compare jamais le MAE du challenger au MAE que le champion affichait à son propre
       entraînement : le champion vieillit, ce chiffre est périmé.
    2. Battre la persistance. Sinon le modèle ne justifie pas sa complexité (EX-05).
    3. Battre le champion de `MIN_GAIN` (2 %). En dessous, l'écart est de l'ordre du bruit lié à la seed
       (voir README : seeds 42 et 7).

Sans `--apply`, la commande affiche sa décision et ne change rien. Avec `--apply`, un membre du binôme valide :
l'alias `champion` bouge, l'ancien champion devient `previous-champion` (retour arrière), et la version porte
un tag `promotion` (décision, fenêtre, qui a validé).

Usage :
    python -m velov.promote [--data data/velov_history.csv | --db data/velov.duckdb] [--test-days 14] [--apply]
"""

from __future__ import annotations

import argparse
import subprocess  # noqa: S404 (git config, lecture seule)
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from velov.contract import ContractError, validate_gold
from velov.features import make_training_frame
from velov.registry import Package, RegistryError, client, fetch, version_of
from velov.tracking import MODEL_NAME
from velov.train import DEFAULT_DB, evaluate, load_raw, temporal_split

MIN_GAIN = 0.02


@dataclass(frozen=True)
class Decision:
    promote: bool
    reason: str


def decide(challenger: dict, champion: dict | None, min_gain: float = MIN_GAIN) -> Decision:
    """Règle de promotion. `challenger` et `champion` : MAE mesurées sur la MÊME fenêtre."""
    if challenger["mae_model"] >= challenger["mae_baseline_persistence"]:
        return Decision(
            False,
            f"ne bat pas la persistance (MAE {challenger['mae_model']:.3f} >= "
            f"{challenger['mae_baseline_persistence']:.3f})",
        )
    if champion is None:
        return Decision(True, "premier champion")
    target = champion["mae_model"] * (1 - min_gain)
    if challenger["mae_model"] <= target:
        return Decision(True, f"MAE {challenger['mae_model']:.3f} <= {target:.3f} (champion - {min_gain:.0%})")
    return Decision(
        False, f"gain insuffisant : MAE {challenger['mae_model']:.3f} > {target:.3f} (champion - {min_gain:.0%})"
    )


def _window(data: Path | None, db: Path, test_days: int):
    raw = validate_gold(load_raw(data, db))  # même contrat qu'à l'entraînement (EX-12)
    _, test_df = temporal_split(make_training_frame(raw), test_days)
    return test_df


def _score(package: Package, test_df) -> dict[str, float]:
    return evaluate(package.load_pipeline(), test_df)


def _validator() -> str:
    try:
        out = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, check=False)  # noqa: S607
    except OSError:
        return "unknown"
    return out.stdout.strip() or "unknown"


def run(
    data: Path | None, db: Path, test_days: int, *, apply: bool = False, validated_by: str | None = None
) -> Decision:
    """Évalue challenger et champion sur la même fenêtre, décide, et applique si `apply`.

    Raises:
        RegistryError: pas de challenger, ou paquet invalide.
        ContractError: la table de test viole le data contract.
    """
    c = client()
    test_df = _window(data, db, test_days)
    start, end = test_df["timestamp"].min(), test_df["timestamp"].max()
    print(f"Fenêtre de test : {start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M} UTC, {len(test_df)} lignes")

    with tempfile.TemporaryDirectory() as tmp:
        challenger = fetch("challenger", Path(tmp) / "challenger", c)
        champion_mv = version_of("champion", c)
        if champion_mv is not None and str(champion_mv.version) == challenger.version:
            print(f"La version {challenger.version} est déjà @champion : rien à promouvoir.")
            return Decision(False, "déjà champion")
        champion = fetch("champion", Path(tmp) / "champion", c) if champion_mv is not None else None
        c_scores = _score(challenger, test_df)
        champ_scores = _score(champion, test_df) if champion else None

    print(f"challenger v{challenger.version:<3}: MAE {c_scores['mae_model']:.3f}")
    if champion and champ_scores:
        print(f"champion   v{champion.version:<3}: MAE {champ_scores['mae_model']:.3f}")
    print(f"persistance     : MAE {c_scores['mae_baseline_persistence']:.3f}")

    decision = decide(c_scores, champ_scores)
    print(f"Décision : {'PROMOUVOIR' if decision.promote else 'GARDER LE CHAMPION'} ({decision.reason})")

    if not apply:
        print("Simulation : relancer avec --apply pour appliquer (validation humaine).")
        return decision

    tags = {
        "promotion_window": f"{start:%Y-%m-%dT%H:%M}/{end:%Y-%m-%dT%H:%M} UTC",
        "promotion_reason": decision.reason,
        "promotion_validated_by": validated_by or _validator(),
        "promotion_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if not decision.promote:
        c.set_model_version_tag(MODEL_NAME, challenger.version, "promotion", "rejected")
        for key, value in tags.items():
            c.set_model_version_tag(MODEL_NAME, challenger.version, key, value)
        return decision
    if champion is not None:
        c.set_registered_model_alias(MODEL_NAME, "previous-champion", champion.version)
    c.set_registered_model_alias(MODEL_NAME, "champion", challenger.version)
    c.delete_registered_model_alias(MODEL_NAME, "challenger")
    c.set_model_version_tag(MODEL_NAME, challenger.version, "promotion", "promoted")
    for key, value in tags.items():
        c.set_model_version_tag(MODEL_NAME, challenger.version, key, value)
    print(
        f"Version {challenger.version} promue @champion"
        + (f" (ancien : @previous-champion = v{champion.version})." if champion else ".")
    )
    return decision


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Décide si le challenger devient champion (affiche, n'applique qu'avec --apply)."
    )
    parser.add_argument("--data", type=Path, help="CSV du simulateur ; sans cette option, on lit la table gold")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="base DuckDB contenant la table gold")
    parser.add_argument("--test-days", type=int, default=14, help="fenêtre d'évaluation commune (jours)")
    parser.add_argument("--apply", action="store_true", help="appliquer la décision (validation humaine)")
    parser.add_argument("--by", help="qui valide (défaut : git config user.name)")
    args = parser.parse_args()
    try:
        decision = run(args.data, args.db, args.test_days, apply=args.apply, validated_by=args.by)
    except (RegistryError, ContractError, FileNotFoundError) as exc:
        sys.exit(f"Promotion impossible : {exc}")
    sys.exit(0 if decision.promote or not args.apply else 1)


if __name__ == "__main__":
    main()
