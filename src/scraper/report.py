"""Génération du rapport JSON à chaque lancement du scraper."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .models import Film

logger = logging.getLogger(__name__)


def _format_size(film: Film) -> str:
    """Retourne la taille lisible : texte brut si disponible, sinon formaté depuis size_gb."""
    if film.size_raw:
        return film.size_raw
    if film.size_gb is not None:
        return f"{film.size_gb:.2f} Go"
    return "inconnue"


def save_report(films: list[Film], site: str, output_dir: Path) -> Path:
    """Écrit scraping-cpasbien-<date>.json dans output_dir et renvoie le chemin."""
    now = datetime.now()
    filename = f"scraping-cpasbien-{now.strftime('%Y-%m-%d_%H-%M-%S')}.json"
    dest = output_dir / filename

    payload = {
        "site": site,
        "date": now.strftime("%Y-%m-%d %H:%M:%S"),
        "Fichiers": [
            {
                "Fichier": film.title,
                "Taille": _format_size(film),
            }
            for film in films
        ],
    }

    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Rapport JSON enregistré : %s (%d films)", dest.name, len(films))
    return dest
