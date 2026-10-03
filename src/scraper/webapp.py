"""Dashboard web Flask — Scraping, Films, Transmission, Authentification JWT."""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, Response, jsonify, request

from .config import Settings
from .enrichment import enrich_report
from .html_report import serve_html
from .naming import sanitize_filename
from .pipeline import run as pipeline_run
from .server import _update_json

logger = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Helpers JWT (PyJWT avec fallback stdlib hmac/hashlib/base64)
# ---------------------------------------------------------------------------

def _create_jwt(payload: dict, secret: str) -> str:
    try:
        import jwt as pyjwt
        return pyjwt.encode(payload, secret, algorithm="HS256")
    except ImportError:
        header = {"alg": "HS256", "typ": "JWT"}
        def _b64e(b: bytes) -> str:
            return base64.urlsafe_b64encode(b).rstrip(b'=').decode('ascii')
        h_b64 = _b64e(json.dumps(header, separators=(',', ':')).encode('utf-8'))
        p_b64 = _b64e(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
        sig_input = f"{h_b64}.{p_b64}".encode('utf-8')
        sig = hmac.new(secret.encode('utf-8'), sig_input, hashlib.sha256).digest()
        return f"{h_b64}.{p_b64}.{_b64e(sig)}"


def _verify_jwt(token: str, secret: str) -> dict | None:
    try:
        import jwt as pyjwt
        return pyjwt.decode(token, secret, algorithms=["HS256"])
    except Exception:
        pass
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        h_b64, p_b64, s_b64 = parts
        sig_input = f"{h_b64}.{p_b64}".encode('utf-8')
        expected_sig = hmac.new(secret.encode('utf-8'), sig_input, hashlib.sha256).digest()
        pad_s = '=' * ((4 - len(s_b64) % 4) % 4)
        actual_sig = base64.urlsafe_b64decode((s_b64 + pad_s).encode('ascii'))
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        pad_p = '=' * ((4 - len(p_b64) % 4) % 4)
        payload_raw = base64.urlsafe_b64decode((p_b64 + pad_p).encode('ascii'))
        payload = json.loads(payload_raw.decode('utf-8'))
        if "exp" in payload and time.time() > payload["exp"]:
            return None
        return payload
    except Exception:
        return None


def _get_request_token() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    if "token" in request.args:
        return request.args.get("token")
    return request.cookies.get("jwt_token")


# ---------------------------------------------------------------------------
# Middlewares & Sécurité
# ---------------------------------------------------------------------------

PUBLIC_PATHS = {"/api/login", "/favicon.ico"}

@app.before_request
def _enforce_security():
    if request.method == "OPTIONS":
        return None
    if request.path in PUBLIC_PATHS or request.path.startswith("/static/"):
        return None
    
    # Sécurise toutes les routes API et actions sensibles
    if request.path.startswith("/api/") or request.path.startswith("/select") or request.path.startswith("/films/"):
        token = _get_request_token()
        cfg = Settings()
        if not token or not _verify_jwt(token, cfg.jwt_secret):
            return jsonify({"error": "Authentification requise", "code": "UNAUTHORIZED"}), 401


# ---------------------------------------------------------------------------
# État global du job (un seul job à la fois)
# ---------------------------------------------------------------------------

_job_lock = threading.Lock()
_job = {"status": "idle", "queue": queue.Queue()}

_active_report: Path | None = None
_active_report_lock = threading.Lock()


def _set_active_report(path: Path) -> None:
    global _active_report
    with _active_report_lock:
        _active_report = path


def _get_active_report() -> Path | None:
    with _active_report_lock:
        return _active_report


# ---------------------------------------------------------------------------
# Routes — Authentification & Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return _DASHBOARD_HTML


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    cfg = Settings()

    if username == cfg.admin_user and password == cfg.admin_pass:
        now = int(time.time())
        exp = now + (cfg.jwt_expires_hours * 3600)
        payload = {"sub": username, "iat": now, "exp": exp}
        token = _create_jwt(payload, cfg.jwt_secret)
        return jsonify({
            "ok": True,
            "token": token,
            "username": username,
            "expires_in": cfg.jwt_expires_hours * 3600
        })
    return jsonify({"ok": False, "error": "Identifiant ou mot de passe incorrect"}), 401


@app.route("/api/me")
def api_me():
    token = _get_request_token()
    cfg = Settings()
    payload = _verify_jwt(token, cfg.jwt_secret) if token else None
    if payload:
        return jsonify({"authenticated": True, "username": payload.get("sub")})
    return jsonify({"authenticated": False}), 401


@app.route("/api/config")
def get_config():
    cfg = Settings()
    return jsonify({
        "base_url":          cfg.base_url,
        "max_films":         cfg.max_films,
        "years":             cfg.years_raw,
        "max_size_gb":       cfg.max_size_gb,
        "output_dir":        cfg.output_dir,
        "headless":          cfg.headless,
        "tmdb_bearer_token": cfg.tmdb_bearer_token,
        "tmdb_api_key":      cfg.tmdb_api_key,
        "rico_api_url":      cfg.rico_api_url,
        "transmission_url":  cfg.transmission_url,
        "transmission_user": cfg.transmission_user,
        "transmission_pass": cfg.transmission_pass,
        "transmission_dir":  cfg.transmission_dir,
    })

# ---------------------------------------------------------------------------
# Routes — API Scraping
# ---------------------------------------------------------------------------

@app.route("/api/scrape/start", methods=["POST"])
def scrape_start():
    with _job_lock:
        if _job["status"] == "running":
            return jsonify({"error": "Un job est déjà en cours"}), 409

        body = request.json or {}
        cfg = Settings()

        overrides = {
            "base_url":          body.get("base_url") or cfg.base_url,
            "max_films":         int(body.get("max_films", cfg.max_films)),
            "years_raw":         body.get("years") if body.get("years") is not None else cfg.years_raw,
            "output_dir":        body.get("output_dir") or cfg.output_dir,
            "headless":          bool(body.get("headless", cfg.headless)),
            "rico_api_url":      body.get("rico_api_url") or cfg.rico_api_url,
            "tmdb_bearer_token": body.get("tmdb_bearer_token") or cfg.tmdb_bearer_token,
            "tmdb_api_key":      body.get("tmdb_api_key") or cfg.tmdb_api_key,
        }

        q = queue.Queue()
        _job["status"] = "running"
        _job["queue"] = q

        def _worker():
            def log_cb(text: str, level: str = "info"):
                q.put({"type": "log", "text": text, "level": level})

            try:
                log_cb("Initialisation du scraping...", "info")
                active_cfg = Settings(**overrides)

                def pipeline_progress(event: dict):
                    msg_type = event.get("type", "")
                    if msg_type == "browser_launched":
                        log_cb("Lancement du navigateur...", "info")
                    elif msg_type == "films_found":
                        log_cb(f"Liste analysée : {event.get('count', 0)} films trouvés, {event.get('selected', 0)} retenus.", "info")
                    elif msg_type == "report_saved":
                        log_cb(f"Rapport sauvegardé : {event.get('path')}", "info")
                    elif msg_type == "torrent_ok":
                        log_cb(f"[{event.get('index')}/{event.get('total')}] Torrent téléchargé : {event.get('title')}", "success")
                    elif msg_type == "torrent_error":
                        log_cb(f"[{event.get('index')}/{event.get('total')}] Échec torrent : {event.get('title')} ({event.get('error')})", "error")
                    elif msg_type == "scrape_done":
                        log_cb("Scraping des films terminé.", "success")

                pipeline_run(settings=active_cfg, progress=pipeline_progress)

                log_cb("Scraping terminé. Recherche du fichier JSON généré...", "info")
                json_name = _find_latest_json(active_cfg.output_dir)
                json_path = Path(active_cfg.output_dir) / json_name
                log_cb(f"Enrichissement du rapport : {json_name}...", "info")

                log_cb("Enrichissement du rapport (Rico & TMDB)...", "info")
                enrich_report(json_path=json_path, settings=active_cfg)

                _set_active_report(json_path)
                log_cb(f"Traitement terminé avec succès ! Rapport : {json_name}", "success")
                q.put({"type": "all_done", "report_name": json_name})
            except Exception as exc:
                logger.exception("Erreur durant le scraping")
                log_cb(f"ERREUR : {exc}", "error")
                q.put({"type": "error", "error": str(exc)})
            finally:
                with _job_lock:
                    _job["status"] = "idle"

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return jsonify({"ok": True, "message": "Job démarré"})


@app.route("/api/scrape/progress")
def scrape_progress():
    q = _job["queue"]

    def generate():
        while True:
            try:
                msg = q.get(timeout=30)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg.get("type") in ("all_done", "error"):
                    break
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _find_latest_json(output_dir: str) -> str:
    files = sorted(Path(output_dir).glob("scraping-cpasbien-*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise RuntimeError("Aucun fichier JSON trouvé après le scraping.")
    return files[-1].name


# ---------------------------------------------------------------------------
# Routes — API Rapports / Films
# ---------------------------------------------------------------------------

@app.route("/api/reports")
def list_reports():
    cfg = Settings()
    output_dir = Path(cfg.output_dir)
    files = sorted(
        output_dir.glob("scraping-cpasbien-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    active = _get_active_report()
    return jsonify([{
        "name": f.name,
        "active": active is not None and f.resolve() == active.resolve(),
    } for f in files])


@app.route("/api/report/activate", methods=["POST"])
def activate_report():
    name = (request.json or {}).get("name", "")
    cfg = Settings()
    path = Path(cfg.output_dir) / name
    if not path.exists():
        return jsonify({"error": "Fichier introuvable"}), 404
    _set_active_report(path)
    return jsonify({"ok": True})


@app.route("/films/<name>")
def serve_report(name: str):
    cfg = Settings()
    output_dir = Path(cfg.output_dir)
    path = output_dir / name
    if not path.exists():
        return "Rapport introuvable", 404
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("Fichiers", []):
        fichier = entry.get("Fichier", "")
        entry["torrent_ok"] = (output_dir / f"{sanitize_filename(fichier)}.torrent").exists()
    _set_active_report(path)
    return serve_html(data)


@app.route("/api/report-json/<name>")
def get_report_json(name: str):
    cfg = Settings()
    output_dir = Path(cfg.output_dir)
    path = output_dir / name
    if not path.exists():
        return jsonify({"error": "Introuvable"}), 404
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("Fichiers", []):
        fichier = entry.get("Fichier", "")
        torrent_path = output_dir / f"{sanitize_filename(fichier)}.torrent"
        entry["torrent_ok"] = torrent_path.exists()
    return jsonify(data)


@app.route("/select", methods=["POST"])
def toggle_select():
    body = request.json or {}
    fichier    = body.get("fichier", "")
    selectionne = bool(body.get("selectionne", False))
    active = _get_active_report()
    if not active:
        return jsonify({"error": "Aucun rapport actif"}), 400
    ok = _update_json(active, threading.Lock(), fichier, selectionne)
    if not ok:
        return jsonify({"error": "Film introuvable"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Routes — Transmission
# ---------------------------------------------------------------------------

@app.route("/api/transmission/test")
def transmission_test():
    body = request.args
    cfg = Settings()
    url  = body.get("url",  cfg.transmission_url)
    user = body.get("user", cfg.transmission_user)
    pwd  = body.get("pass", cfg.transmission_pass)
    try:
        _transmission_session_id(url, user, pwd)
        return jsonify({"ok": True, "message": "Connexion réussie"})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)})


@app.route("/api/transmission/send", methods=["POST"])
def transmission_send():
    body = request.json or {}
    cfg = Settings()
    trans_url  = body.get("url",  cfg.transmission_url)
    trans_user = body.get("user", cfg.transmission_user)
    trans_pass = body.get("pass", cfg.transmission_pass)
    trans_dir  = body.get("dir",  cfg.transmission_dir)
    films      = body.get("films", [])

    if not films:
        return jsonify({"error": "Aucun film sélectionné"}), 400

    output_dir = Path(cfg.output_dir)

    fichier_by_film: dict[str, str] = {}
    active = _get_active_report()
    if active and active.exists():
        try:
            data = json.loads(active.read_text(encoding="utf-8"))
            for entry in data.get("Fichiers", []):
                key = entry.get("film") or entry.get("Fichier", "")
                fichier_by_film[key] = entry.get("Fichier", key)
        except Exception:
            pass

    results = []
    for film_name in films:
        fichier = fichier_by_film.get(film_name, film_name)
        torrent_path = output_dir / f"{sanitize_filename(fichier)}.torrent"
        if not torrent_path.exists():
            results.append({"film": film_name, "ok": False, "error": f"Fichier .torrent introuvable : {torrent_path}"})
            continue
        try:
            resp = _send_torrent(torrent_path, trans_url, trans_user, trans_pass, trans_dir)
            results.append({"film": film_name, "ok": True, "result": resp})
        except Exception as exc:
            results.append({"film": film_name, "ok": False, "error": str(exc)})

    return jsonify({"results": results})


def _transmission_session_id(base_url: str, user: str, pwd: str) -> str:
    rpc_url = base_url.rstrip("/") + "/transmission/rpc"
    req = urllib.request.Request(rpc_url, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    if user:
        creds = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
    try:
        urllib.request.urlopen(req, timeout=8)
        return ""
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return exc.headers.get("X-Transmission-Session-Id", "")
        raise RuntimeError(f"Transmission HTTP {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"Impossible de joindre Transmission : {exc}") from exc


def _send_torrent(torrent_path: Path, base_url: str, user: str, pwd: str, dest_dir: str) -> dict:
    session_id = _transmission_session_id(base_url, user, pwd)
    rpc_url = base_url.rstrip("/") + "/transmission/rpc"

    metainfo = base64.b64encode(torrent_path.read_bytes()).decode()
    payload = json.dumps({
        "method": "torrent-add",
        "arguments": {"metainfo": metainfo, "download-dir": dest_dir},
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "X-Transmission-Session-Id": session_id,
    }
    if user:
        creds = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"

    req = urllib.request.Request(rpc_url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"Transmission {exc.code}: {body[:200]}") from exc


# ---------------------------------------------------------------------------
# Dashboard HTML (inline avec Modal Login JWT)
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Torrent Scraper — Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0f172a;
      --card: #1e293b;
      --card-border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --success: #10b981;
      --error: #ef4444;
      --warning: #f59e0b;
      --radius: 12px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', system-ui, sans-serif; }
    body { background: var(--bg); color: var(--text); min-height: 100vh; padding: 24px; }
    .container { max-width: 1200px; margin: 0 auto; }
    
    /* Header & Navigation */
    header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--card-border); }
    h1 { font-size: 1.5rem; font-weight: 700; background: linear-gradient(135deg, #60a5fa, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .user-badge { display: flex; align-items: center; gap: 12px; font-size: 0.9rem; color: var(--muted); background: rgba(30, 41, 59, 0.6); padding: 6px 14px; border-radius: 20px; border: 1px solid var(--card-border); }
    .user-badge strong { color: var(--text); }
    
    /* Buttons & Inputs */
    .btn { padding: 8px 16px; border-radius: 8px; border: none; font-weight: 600; cursor: pointer; transition: all 0.2s ease; display: inline-flex; align-items: center; gap: 8px; }
    .btn-primary { background: linear-gradient(135deg, #3b82f6, #6366f1); color: white; box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3); }
    .btn-primary:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 6px 16px rgba(59, 130, 246, 0.4); }
    .btn-danger { background: var(--error); color: white; }
    .btn-outline { background: transparent; border: 1px solid var(--card-border); color: var(--text); }
    .btn-outline:hover { background: var(--card); border-color: var(--muted); }
    .btn-sm { padding: 4px 10px; font-size: 0.82rem; }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    
    /* Tabs */
    .nav-tabs { display: flex; gap: 8px; margin-bottom: 24px; border-bottom: 1px solid var(--card-border); padding-bottom: 8px; }
    .tab-btn { background: transparent; border: none; color: var(--muted); padding: 8px 16px; font-weight: 500; border-radius: 8px; cursor: pointer; transition: all 0.2s; }
    .tab-btn:hover { color: var(--text); background: rgba(255,255,255,0.05); }
    .tab-btn.active { color: white; background: var(--accent); }
    .tab-pane { display: none; }
    .tab-pane.active { display: block; }
    
    /* Forms & Cards */
    .card { background: var(--card); border: 1px solid var(--card-border); border-radius: var(--radius); padding: 24px; margin-bottom: 24px; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .form-group { margin-bottom: 16px; }
    label { display: block; font-size: 0.85rem; font-weight: 500; color: var(--muted); margin-bottom: 6px; }
    input[type="text"], input[type="password"], input[type="number"], select { width: 100%; padding: 10px 14px; background: #0f172a; border: 1px solid var(--card-border); border-radius: 8px; color: var(--text); outline: none; transition: border-color 0.2s; }
    input:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2); }
    .checkbox-group { display: flex; align-items: center; gap: 8px; cursor: pointer; }
    
    /* Log console */
    .log-container { background: #090d16; border: 1px solid var(--card-border); border-radius: 8px; padding: 16px; font-family: monospace; font-size: 0.85rem; height: 320px; overflow-y: auto; color: #a7f3d0; margin-top: 16px; }
    .log-line { margin-bottom: 4px; word-break: break-all; }
    .log-error { color: #fca5a5; }
    .log-success { color: #6ee7b7; font-weight: 600; }
    
    /* Glassmorphism Login Modal */
    .modal-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(16px); display: flex; align-items: center; justify-content: center; z-index: 9999; opacity: 0; pointer-events: none; transition: opacity 0.3s ease; }
    .modal-overlay.active { opacity: 1; pointer-events: auto; }
    .login-box { background: rgba(30, 41, 59, 0.95); border: 1px solid rgba(255, 255, 255, 0.1); box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7), 0 0 30px rgba(59, 130, 246, 0.25); border-radius: 16px; padding: 36px; width: 100%; max-width: 420px; text-align: center; }
    .login-box h2 { font-size: 1.6rem; font-weight: 700; margin-bottom: 8px; background: linear-gradient(135deg, #60a5fa, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .login-box p { color: var(--muted); font-size: 0.9rem; margin-bottom: 24px; }
    .login-box .form-group { text-align: left; }
    .login-box .btn-primary { width: 100%; padding: 12px; justify-content: center; font-size: 1rem; margin-top: 12px; }
    .error-alert { background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; padding: 10px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 16px; display: none; }
    
    /* Toast */
    #toast { position: fixed; bottom: 24px; right: 24px; padding: 12px 20px; border-radius: 8px; background: var(--card); border: 1px solid var(--card-border); box-shadow: 0 10px 25px rgba(0,0,0,0.5); z-index: 10000; opacity: 0; transform: translateY(20px); transition: all 0.3s ease; pointer-events: none; }
    #toast.show { opacity: 1; transform: translateY(0); }
    #toast.ok { border-color: var(--success); color: #6ee7b7; }
    #toast.error { border-color: var(--error); color: #fca5a5; }
    
    /* Report iframe container */
    iframe { width: 100%; height: 750px; border: none; border-radius: 8px; background: white; }
    .report-selector { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
  </style>
</head>
<body>

  <!-- Modal Authentification JWT -->
  <div id="login-modal" class="modal-overlay">
    <div class="login-box">
      <h2>🔐 Connexion</h2>
      <p>Accès sécurisé au Dashboard Torrent Scraper</p>
      <div id="login-error" class="error-alert"></div>
      <form id="form-login">
        <div class="form-group">
          <label for="login-username">Utilisateur</label>
          <input type="text" id="login-username" placeholder="ex: admin" required autocomplete="username">
        </div>
        <div class="form-group">
          <label for="login-password">Mot de passe</label>
          <input type="password" id="login-password" placeholder="••••••••" required autocomplete="current-password">
        </div>
        <button type="submit" id="btn-login-submit" class="btn btn-primary">Se connecter 🚀</button>
      </form>
    </div>
  </div>

  <div class="container">
    <header>
      <h1>🎬 Torrent Scraper Dashboard</h1>
      <div id="user-badge" class="user-badge" style="display:none;">
        👤 <strong id="logged-user">admin</strong>
        <button id="btn-logout" class="btn btn-sm btn-outline">Déconnexion</button>
      </div>
    </header>

    <nav class="nav-tabs">
      <button class="tab-btn active" data-tab="scraping">⚡ Scraping</button>
      <button class="tab-btn" data-tab="reports">📊 Rapports Films</button>
      <button class="tab-btn" data-tab="transmission">📡 Transmission</button>
    </nav>

    <!-- TAB 1 : SCRAPING -->
    <section id="tab-scraping" class="tab-pane active">
      <div class="card">
        <h2>Lancer un nouveau Scraping</h2>
        <div class="grid-2" style="margin-top: 16px;">
          <div class="form-group">
            <label>URL du site cible</label>
            <input type="text" id="p-base-url" placeholder="https://www.cpasbien2.cc/category/films">
          </div>
          <div class="form-group">
            <label>Nombre max de films</label>
            <input type="number" id="p-max-films" value="100">
          </div>
        </div>
        <div class="grid-2">
          <div class="form-group">
            <label>Années recherchées (séparées par virgule)</label>
            <input type="text" id="p-years" value="2025,2026">
          </div>
          <div class="form-group">
            <label>Dossier de sortie (.torrent)</label>
            <input type="text" id="p-output-dir" value="downloads">
          </div>
        </div>
        <div class="grid-2">
          <div class="form-group">
            <label>API Rico (Vérification présence)</label>
            <input type="text" id="p-rico-url" value="http://localhost:3000">
          </div>
          <div class="form-group">
            <label>TMDB Bearer Token</label>
            <input type="password" id="p-tmdb-token" placeholder="Bearer eyJ...">
          </div>
        </div>
        <div class="form-group">
          <label class="checkbox-group">
            <input type="checkbox" id="p-headless">
            <span>Mode sans interface (Headless)</span>
          </label>
        </div>
        
        <div style="display: flex; gap: 12px; margin-top: 12px;">
          <button id="btn-start" class="btn btn-primary">🚀 Démarrer le scraping</button>
          <button id="btn-stop" class="btn btn-danger" disabled>🛑 Arrêter</button>
          <span id="job-badge" class="user-badge" style="display:none; background: rgba(59, 130, 246, 0.2); color: #60a5fa;">Job en cours...</span>
        </div>

        <div class="log-container" id="progress-log">
          <div class="log-line">Console de scraping en attente...</div>
        </div>
      </div>
    </section>

    <!-- TAB 2 : RAPPORTS -->
    <section id="tab-reports" class="tab-pane">
      <div class="card">
        <div class="report-selector">
          <label style="margin-bottom:0;">Sélectionner un rapport :</label>
          <select id="select-report" style="width: auto; min-width: 280px;"></select>
          <button id="btn-refresh-reports" class="btn btn-outline btn-sm">🔄 Actualiser</button>
        </div>
        <iframe id="report-frame" src="about:blank"></iframe>
      </div>
    </section>

    <!-- TAB 3 : TRANSMISSION -->
    <section id="tab-transmission" class="tab-pane">
      <div class="card">
        <h2>Connexion Transmission</h2>
        <div class="grid-2" style="margin-top: 16px;">
          <div class="form-group">
            <label>URL du serveur Transmission RPC</label>
            <input type="text" id="t-url" placeholder="http://ricohoho.fr:9091">
          </div>
          <div class="form-group">
            <label>Dossier de destination sur le serveur</label>
            <input type="text" id="t-dir" placeholder="/home/streaming/films">
          </div>
        </div>
        <div class="grid-2">
          <div class="form-group">
            <label>Utilisateur RPC</label>
            <input type="text" id="t-user" placeholder="transmission">
          </div>
          <div class="form-group">
            <label>Mot de passe RPC</label>
            <input type="password" id="t-pass">
          </div>
        </div>
        <button id="btn-test-trans" class="btn btn-outline" style="margin-bottom: 24px;">🧪 Test de connexion</button>
        
        <h2>Films prêts à être envoyés</h2>
        <div id="trans-films-list" style="margin-top: 12px; margin-bottom: 16px;">
          <p style="color: var(--muted); font-size: 0.9rem;">Chargement des films sélectionnés...</p>
        </div>
        <button id="btn-send-trans" class="btn btn-primary" disabled>📡 Envoyer les torrents vers Transmission</button>
      </div>
    </section>
  </div>

  <div id="toast"></div>

  <script>
    // ─── Gestion de l'authentification JWT ──────────────────────────────────
    function getToken() { return localStorage.getItem('torrent_scraper_token') || ''; }
    function setToken(t) { localStorage.setItem('torrent_scraper_token', t); }
    function clearToken() { localStorage.removeItem('torrent_scraper_token'); }

    const loginModal = document.getElementById('login-modal');
    const loginError = document.getElementById('login-error');
    const userBadge  = document.getElementById('user-badge');
    const loggedUser = document.getElementById('logged-user');

    function showLoginModal() {
      loginError.style.display = 'none';
      loginModal.classList.add('active');
      userBadge.style.display = 'none';
    }

    function hideLoginModal(username) {
      loginModal.classList.remove('active');
      loggedUser.textContent = username || 'admin';
      userBadge.style.display = 'inline-flex';
    }

    async function apiFetch(url, options = {}) {
      options.headers = options.headers || {};
      const token = getToken();
      if (token) {
        options.headers['Authorization'] = 'Bearer ' + token;
      }
      const response = await fetch(url, options);
      if (response.status === 401 && !url.includes('/api/login')) {
        showLoginModal();
        throw new Error('Authentification requise (401)');
      }
      return response;
    }

    document.getElementById('form-login').addEventListener('submit', async (e) => {
      e.preventDefault();
      const user = document.getElementById('login-username').value;
      const pass = document.getElementById('login-password').value;
      const submitBtn = document.getElementById('btn-login-submit');
      
      submitBtn.disabled = true;
      submitBtn.textContent = 'Vérification...';
      loginError.style.display = 'none';

      try {
        const r = await fetch('/api/login', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({username: user, password: pass}),
        });
        const data = await r.json();
        if (data.ok && data.token) {
          setToken(data.token);
          hideLoginModal(data.username);
          showToast('Connexion réussie !', 'ok');
          loadConfig();
          loadReports();
        } else {
          loginError.textContent = data.error || 'Identifiants invalides';
          loginError.style.display = 'block';
        }
      } catch (err) {
        loginError.textContent = 'Erreur lors de la connexion au serveur';
        loginError.style.display = 'block';
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Se connecter 🚀';
      }
    });

    document.getElementById('btn-logout').addEventListener('click', () => {
      clearToken();
      showToast('Déconnecté', 'ok');
      showLoginModal();
    });

    async function checkAuthStatus() {
      const token = getToken();
      if (!token) {
        showLoginModal();
        return;
      }
      try {
        const r = await apiFetch('/api/me');
        const d = await r.json();
        if (d.authenticated) {
          hideLoginModal(d.username);
          loadConfig();
          loadReports();
        } else {
          showLoginModal();
        }
      } catch (err) {
        showLoginModal();
      }
    }

    // ─── Toast UI ────────────────────────────────────────────────────────────
    function showToast(msg, type = 'ok') {
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.className = 'show ' + type;
      setTimeout(() => { t.className = ''; }, 3500);
    }

    // ─── Tabs Navigation ─────────────────────────────────────────────────────
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        if (btn.dataset.tab === 'transmission') refreshTransFilms();
      });
    });

    // ─── Configuration ───────────────────────────────────────────────────────
    function loadConfig() {
      apiFetch('/api/config')
        .then(r => r.json())
        .then(cfg => {
          document.getElementById('p-base-url').value   = cfg.base_url   || '';
          document.getElementById('p-max-films').value  = cfg.max_films  || 100;
          document.getElementById('p-years').value      = cfg.years      || '';
          document.getElementById('p-output-dir').value = cfg.output_dir  || '';
          document.getElementById('p-rico-url').value   = cfg.rico_api_url      || '';
          document.getElementById('p-tmdb-token').value = cfg.tmdb_bearer_token || '';
          document.getElementById('p-headless').checked = !!cfg.headless;
          document.getElementById('t-url').value        = cfg.transmission_url  || '';
          document.getElementById('t-user').value       = cfg.transmission_user || '';
          document.getElementById('t-pass').value       = cfg.transmission_pass || '';
          document.getElementById('t-dir').value        = cfg.transmission_dir  || '';
        })
        .catch(err => console.error('Erreur chargement config :', err));
    }

    // ─── Scraping ────────────────────────────────────────────────────────────
    const logEl    = document.getElementById('progress-log');
    const badge    = document.getElementById('job-badge');
    const btnStart = document.getElementById('btn-start');
    const btnStop  = document.getElementById('btn-stop');

    function appendLog(text, cls = 'log-info') {
      const line = document.createElement('div');
      line.className = 'log-line ' + cls;
      line.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
      logEl.appendChild(line);
      logEl.scrollTop = logEl.scrollHeight;
    }

    btnStart.addEventListener('click', async () => {
      btnStart.disabled = true;
      btnStop.disabled  = false;
      badge.style.display = 'inline-flex';
      logEl.innerHTML = '';
      appendLog('Démarrage du job...', 'log-info');

      const body = {
        base_url:          document.getElementById('p-base-url').value,
        max_films:         document.getElementById('p-max-films').value,
        years:             document.getElementById('p-years').value,
        output_dir:        document.getElementById('p-output-dir').value,
        rico_api_url:      document.getElementById('p-rico-url').value,
        tmdb_bearer_token: document.getElementById('p-tmdb-token').value,
        headless:          document.getElementById('p-headless').checked,
      };

      try {
        const r = await apiFetch('/api/scrape/start', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || 'Erreur au démarrage');
        
        const token = getToken();
        const evSource = new EventSource('/api/scrape/progress?token=' + encodeURIComponent(token));
        evSource.onmessage = (e) => {
          const msg = JSON.parse(e.data);
          if (msg.type === 'log') {
            appendLog(msg.text, 'log-' + (msg.level || 'info'));
          } else if (msg.type === 'all_done') {
            evSource.close();
            btnStart.disabled = false;
            btnStop.disabled  = true;
            badge.style.display = 'none';
            showToast('Scraping terminé avec succès !', 'ok');
            loadReports();
          } else if (msg.type === 'error') {
            evSource.close();
            btnStart.disabled = false;
            btnStop.disabled  = true;
            badge.style.display = 'none';
            showToast('Erreur scraping: ' + msg.error, 'error');
          }
        };
      } catch (err) {
        appendLog('ERREUR : ' + err.message, 'log-error');
        btnStart.disabled = false;
        btnStop.disabled  = true;
        badge.style.display = 'none';
      }
    });

    // ─── Rapports ────────────────────────────────────────────────────────────
    const selectReports = document.getElementById('select-report');
    const reportFrame   = document.getElementById('report-frame');

    async function loadReports() {
      try {
        const r = await apiFetch('/api/reports');
        const reports = await r.json();
        selectReports.innerHTML = '';
        if (!reports.length) {
          selectReports.innerHTML = '<option>Aucun rapport disponible</option>';
          return;
        }
        let activeName = '';
        reports.forEach(rep => {
          const opt = document.createElement('option');
          opt.value = rep.name;
          opt.textContent = rep.name + (rep.active ? ' (Actif)' : '');
          if (rep.active) { opt.selected = true; activeName = rep.name; }
          selectReports.appendChild(opt);
        });
        if (!activeName && reports.length) activeName = reports[0].name;
        if (activeName) loadReportFrame(activeName);
      } catch (err) {
        console.error('Erreur chargement rapports:', err);
      }
    }

    function loadReportFrame(name) {
      reportFrame.src = '/films/' + encodeURIComponent(name) + '?token=' + encodeURIComponent(getToken());
    }

    selectReports.addEventListener('change', async () => {
      const name = selectReports.value;
      if (!name) return;
      await apiFetch('/api/report/activate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name}),
      });
      loadReportFrame(name);
    });

    document.getElementById('btn-refresh-reports').addEventListener('click', loadReports);

    // ─── Transmission ────────────────────────────────────────────────────────
    let _transFilms = [];
    const transListEl  = document.getElementById('trans-films-list');
    const btnSendTrans = document.getElementById('btn-send-trans');

    async function refreshTransFilms() {
      transListEl.innerHTML = '<p style="color: var(--muted);">Chargement...</p>';
      try {
        const r = await apiFetch('/api/reports');
        const reports = await r.json();
        const active = reports.find(r => r.active) || reports[0];
        if (!active) {
          transListEl.innerHTML = '<p style="color: var(--muted);">Aucun rapport actif.</p>';
          btnSendTrans.disabled = true;
          return;
        }
        const rJson = await apiFetch('/api/report-json/' + encodeURIComponent(active.name));
        const data  = await rJson.json();
        _transFilms = (data.Fichiers || []).filter(f => f.selectionne);
        
        if (!_transFilms.length) {
          transListEl.innerHTML = '<p style="color: var(--muted);">Aucun film coché "À télécharger" dans le rapport actif.</p>';
          btnSendTrans.disabled = true;
          return;
        }

        transListEl.innerHTML = '';
        _transFilms.forEach(f => {
          const name = f.film || f.Fichier;
          const torrentOk = f.torrent_ok;
          const div = document.createElement('div');
          div.style.cssText = 'padding: 8px 12px; background: rgba(15,23,42,0.6); border: 1px solid var(--card-border); border-radius: 6px; margin-bottom: 6px; display: flex; align-items: center; justify-content: space-between; font-size: 0.9rem;';
          div.dataset.film = name;
          div.innerHTML = `
            <span>🎬 <strong>${name}</strong></span>
            <span class="status" style="color: ${torrentOk ? 'var(--success)' : 'var(--error)'}">
              ${torrentOk ? '✓ Torrent présent' : '✗ .torrent manquant'}
            </span>
          `;
          transListEl.appendChild(div);
        });
        btnSendTrans.disabled = false;
      } catch (err) {
        transListEl.innerHTML = '<p style="color: var(--error);">Erreur lors de la récupération des films.</p>';
      }
    }

    document.getElementById('btn-test-trans').addEventListener('click', async () => {
      const params = new URLSearchParams({
        url:  document.getElementById('t-url').value,
        user: document.getElementById('t-user').value,
        pass: document.getElementById('t-pass').value,
      });
      try {
        const r = await apiFetch('/api/transmission/test?' + params);
        const d = await r.json();
        showToast(d.message, d.ok ? 'ok' : 'error');
      } catch (err) {
        showToast('Erreur test transmission', 'error');
      }
    });

    btnSendTrans.addEventListener('click', async () => {
      if (!_transFilms.length) return;
      btnSendTrans.disabled = true;
      const films = _transFilms.map(f => f.film || f.Fichier);
      const body = {
        url:   document.getElementById('t-url').value,
        user:  document.getElementById('t-user').value,
        pass:  document.getElementById('t-pass').value,
        dir:   document.getElementById('t-dir').value,
        films: films,
      };
      try {
        const r = await apiFetch('/api/transmission/send', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
        });
        const data = await r.json();
        (data.results || []).forEach(res => {
          const el = transListEl.querySelector(`[data-film="${CSS.escape(res.film)}"]`);
          if (el) {
            el.querySelector('.status').textContent = res.ok ? '✓ Envoyé' : '✗ ' + res.error;
            el.querySelector('.status').style.color = res.ok ? 'var(--success)' : 'var(--error)';
          }
        });
        const ok = (data.results || []).filter(r => r.ok).length;
        showToast(`${ok}/${(data.results || []).length} torrent(s) envoyé(s)`, ok === (data.results || []).length ? 'ok' : 'error');
      } catch (err) {
        showToast('Erreur lors de l’envoi des torrents', 'error');
      } finally {
        btnSendTrans.disabled = false;
      }
    });

    // Démarrage : Vérification de l'authentification
    checkAuthStatus();
  </script>
</body>
</html>
"""