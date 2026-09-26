"""Commande ``torrent-dashboard`` : démarre le dashboard web complet."""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
import threading

from .webapp import app
from .config import Settings


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    cfg = Settings()
    parser = argparse.ArgumentParser(
        prog="torrent-dashboard",
        description="Lance le dashboard web (Scraping, Films, Transmission) sur localhost.",
    )
    parser.add_argument("--port", type=int, default=8765, help="Port HTTP (défaut : 8765).")
    parser.add_argument("--host", default="0.0.0.0", help="Interface d'écoute (défaut : 0.0.0.0).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs détaillés (DEBUG).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    url = f"http://localhost:{args.port}/"
    logging.getLogger(__name__).info("Dashboard : %s  (Ctrl+C pour arrêter)", url)

    # Ouvre le navigateur après un court délai pour laisser Flask démarrer.
    threading.Timer(1.0, webbrowser.open_new_tab, args=[url]).start()

    try:
        app.run(host=args.host, port=args.port, threaded=True, debug=False)
    except KeyboardInterrupt:
        print("\nDashboard arrêté.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
