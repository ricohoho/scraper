"""Tests unitaires des fonctions pures (aucun réseau / navigateur requis).

Exécution : ``python -m pytest`` (ou ``python tests/test_units.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scraper.naming import sanitize_filename  # noqa: E402
from scraper.parsing import parse_size  # noqa: E402


def test_parse_size_variants():
    assert parse_size("1.46 Go") == 1.46
    assert parse_size("2,3 GB") == 2.3
    assert parse_size("950 Mo") == round(950 / 1024, 4)
    assert parse_size("1.2 To") == round(1.2 * 1024, 4)
    assert parse_size("inconnu") is None
    assert parse_size(None) is None


def test_sanitize_filename():
    assert sanitize_filename("Film: 2025 / VF") == "Film 2025 VF"
    assert sanitize_filename("  ...  ") == "torrent"
    assert len(sanitize_filename("x" * 500)) <= 150


if __name__ == "__main__":
    test_parse_size_variants()
    test_sanitize_filename()
    print("OK")
