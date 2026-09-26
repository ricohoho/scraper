"""Point d'entrée ligne de commande. Surcharge la config par des options CLI."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Settings
from .pipeline import run


def _build_settings(args: argparse.Namespace) -> Settings:
    """Construit la config depuis l'environnement, puis applique les surcharges CLI."""
    settings = Settings()
    if args.url:
        settings.base_url = args.url
    if args.max is not None:
        settings.max_films = args.max
    if args.out:
        settings.output_dir = args.out
    if args.headless:
        settings.headless = True
    if args.headful:
        settings.headless = False
    return settings


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="torrent-scraper",
        description="Scrape une liste de films, filtre par année/taille et récupère les .torrent.",
    )
    parser.add_argument("--url", help="URL du site cible (surcharge SCRAPER_BASE_URL).")
    parser.add_argument("--max", type=int, help="Nombre maximum de films à analyser.")
    parser.add_argument("--out", help="Dossier de sortie des .torrent.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="Navigateur sans interface.")
    mode.add_argument("--headful", action="store_true", help="Navigateur visible (défaut).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs détaillés (DEBUG).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    settings = _build_settings(args)
    logging.getLogger(__name__).info("Cible : %s (max %d films)", settings.base_url, settings.max_films)

    result = run(settings)

    print("\n=== Résumé ===")
    print(f"Films analysés   : {len(result.films)}")
    print(f"Films retenus    : {len(result.selected)}")
    print(f"Torrents récupérés : {result.downloaded_count}")
    for outcome in result.downloads:
        if outcome.ok:
            print(f"  ✓ {outcome.saved_path}")
        else:
            print(f"  ✗ {outcome.film.title} — {outcome.error}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
