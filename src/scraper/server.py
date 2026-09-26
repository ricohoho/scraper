"""Serveur HTTP local pour la sélection interactive des films."""

from __future__ import annotations

import json
import logging
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .html_report import serve_html

logger = logging.getLogger(__name__)

PORT = 8765


class _SelectionHandler(BaseHTTPRequestHandler):
    """Handler instancié par HTTPServer à chaque requête.

    json_path et lock sont injectés via la factory _make_handler().
    """

    json_path: Path
    lock: threading.Lock

    # ------------------------------------------------------------------ GET --

    def do_GET(self) -> None:
        if self.path not in ("/", "/index.html"):
            self._send(404, "text/plain", b"Not found")
            return
        data = json.loads(self.json_path.read_text(encoding="utf-8"))
        body = serve_html(data).encode("utf-8")
        self._send(200, "text/html; charset=utf-8", body)

    # ----------------------------------------------------------------- POST --

    def do_POST(self) -> None:
        if self.path != "/select":
            self._send(404, "text/plain", b"Not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            fichier    = payload["fichier"]
            selectionne = bool(payload["selectionne"])
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._send(400, "application/json", json.dumps({"error": str(exc)}).encode())
            return

        updated = _update_json(self.json_path, self.lock, fichier, selectionne)
        if not updated:
            self._send(404, "application/json", b'{"error": "film introuvable"}')
            return

        self._send(200, "application/json", b'{"ok": true}')

    # --------------------------------------------------------------- helpers --

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug(fmt, *args)


def _make_handler(json_path: Path, lock: threading.Lock) -> type:
    """Retourne une sous-classe de _SelectionHandler avec json_path et lock injectés."""
    class _H(_SelectionHandler):
        pass
    _H.json_path = json_path
    _H.lock = lock
    return _H


def _update_json(json_path: Path, lock: threading.Lock, fichier: str, selectionne: bool) -> bool:
    """Met à jour `selectionne` sur le film identifié par `fichier`. Renvoie False si introuvable."""
    with lock:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        for item in data.get("Fichiers", []):
            if item.get("Fichier") == fichier:
                item["selectionne"] = selectionne
                json_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return True
    return False


def run_server(json_path: Path, port: int = PORT) -> None:
    """Démarre le serveur HTTP, ouvre le navigateur, tourne jusqu'à Ctrl+C."""
    handler = _make_handler(json_path, threading.Lock())
    try:
        httpd = HTTPServer(("localhost", port), handler)
    except OSError as exc:
        logger.error(
            "Impossible de démarrer le serveur sur le port %d : %s\n"
            "Essayez un autre port avec --port.",
            port, exc,
        )
        raise

    url = f"http://localhost:{port}/"
    logger.info("Serveur démarré sur %s  (Ctrl+C pour arrêter)", url)
    webbrowser.open_new_tab(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServeur arrêté.")
    finally:
        httpd.server_close()
