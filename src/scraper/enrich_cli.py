"""Commande ``torrent-enrich`` : enrichit un rapport JSON issu du scraper."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import Settings
from .enrichment import enrich_report


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="torrent-enrich",
        description=(
            "Enrichit un rapport JSON : extrait titre/année, vérifie sur Rico, "
            "complète les films absents via TMDB."
        ),
    )
    parser.add_argument("json_file", help="Chemin vers le fichier scraping-cpasbien-*.json.")
    parser.add_argument(
        "--rico-url",
        default=None,
        help="URL de base du webservice Rico (ex. http://localhost:3000). "
             "Surcharge SCRAPER_RICO_API_URL.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs détaillés (DEBUG).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    json_path = Path(args.json_file)
    if not json_path.exists():
        logging.getLogger(__name__).error("Fichier introuvable : %s", json_path)
        return 1

    settings = Settings()
    if args.rico_url:
        settings.rico_api_url = args.rico_url

    enrich_report(json_path, settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
