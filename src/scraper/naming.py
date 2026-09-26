"""Génération de noms de fichiers sûrs à partir des titres de films."""

from __future__ import annotations

import re
from pathlib import Path

# Caractères interdits sous Windows/Unix dans un nom de fichier.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTISPACE = re.compile(r"\s+")
_MAX_STEM_LEN = 150


def sanitize_filename(title: str) -> str:
    """Transforme un titre en nom de fichier (sans extension), sûr et lisible."""
    cleaned = _FORBIDDEN.sub(" ", title)
    cleaned = _MULTISPACE.sub(" ", cleaned).strip().strip(".")
    if not cleaned:
        cleaned = "torrent"
    return cleaned[:_MAX_STEM_LEN].strip()


def unique_torrent_path(output_dir: Path, title: str) -> Path:
    """Chemin ``<output_dir>/<titre>.torrent`` (écrase l'existant si présent)."""
    return output_dir / f"{sanitize_filename(title)}.torrent"
