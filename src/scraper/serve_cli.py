"""Commande ``torrent-serve`` : ouvre un rapport JSON avec sélection interactive."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .server import PORT, run_server


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="torrent-serve",
        description=(
            "Ouvre un rapport JSON enrichi dans le navigateur avec sélection interactive. "
            "Chaque clic sur une checkbox met à jour le fichier JSON immédiatement."
        ),
    )
    parser.add_argument("json_file", help="Chemin vers le fichier scraping-cpasbien-*.json enrichi.")
    parser.add_argument(
        "--port",
        type=int,
        default=PORT,
        help=f"Port HTTP local (défaut : {PORT}).",
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

    try:
        run_server(json_path, port=args.port)
    except OSError:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
