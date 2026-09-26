"""Dashboard web Flask — Scraping, Films, Transmission."""

from __future__ import annotations

import base64
import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, Response, jsonify, request

from .config import Settings
from .enrichment import enrich_report
from .html_report import serve_html
from .naming import sanitize_filename
from .pipeline import run as pipeline_run
from .server import _update_json  # réutilise la fonction thread-safe existante

logger = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# État global du job (un seul job à la fois)
# ---------------------------------------------------------------------------

_job_lock = threading.Lock()
_job = {"status": "idle", "queue": queue.Queue()}

# Dernier rapport JSON actif (mis à jour après chaque scraping)
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
# Routes — Dashboard HTML
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return _DASHBOARD_HTML


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
            return jsonify({"error": "Un job est déjà en cours."}), 409
        _job["status"] = "running"
        _job["queue"] = queue.Queue()

    body = request.json or {}
    settings = Settings()
    if body.get("base_url"):      settings.base_url     = body["base_url"]
    if body.get("max_films"):     settings.max_films    = int(body["max_films"])
    if body.get("years"):         settings.years_raw    = body["years"]
    if body.get("max_size_gb"):   settings.max_size_gb  = float(body["max_size_gb"])
    if body.get("output_dir"):    settings.output_dir   = body["output_dir"]
    if "headless" in body:        settings.headless     = bool(body["headless"])
    if body.get("tmdb_bearer_token"): settings.tmdb_bearer_token = body["tmdb_bearer_token"]
    if body.get("tmdb_api_key"):  settings.tmdb_api_key = body["tmdb_api_key"]
    if body.get("rico_api_url"):  settings.rico_api_url = body["rico_api_url"]

    q = _job["queue"]

    def _run():
        try:
            result = pipeline_run(settings, progress=q.put)
            report_path = Path(settings.output_dir) / _find_latest_json(settings.output_dir)
            _set_active_report(report_path)
            q.put({"type": "enriching", "message": "Enrichissement Rico + TMDB en cours…"})
            enrich_report(report_path, settings)
            q.put({"type": "all_done",
                   "report": report_path.name,
                   "downloaded": result.downloaded_count})
        except Exception as exc:
            logger.exception("Erreur dans le job de scraping")
            q.put({"type": "error", "message": str(exc)})
        finally:
            with _job_lock:
                _job["status"] = "idle"

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/scrape/progress")
def scrape_progress():
    def generate():
        q = _job["queue"]
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
    """Retourne le nom du fichier JSON le plus récent dans output_dir."""
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
    """Renvoie le contenu brut du JSON pour un rapport donné, avec torrent_ok par entrée."""
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
    films      = body.get("films", [])  # liste de valeurs "film" (titre nettoyé)

    if not films:
        return jsonify({"error": "Aucun film sélectionné"}), 400

    output_dir = Path(cfg.output_dir)

    # Construire un index film_name → Fichier à partir du rapport actif
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


# ---------------------------------------------------------------------------
# Transmission RPC (urllib stdlib)
# ---------------------------------------------------------------------------

def _transmission_session_id(base_url: str, user: str, pwd: str) -> str:
    """Obtient le X-Transmission-Session-Id en déclenchant un 409."""
    rpc_url = base_url.rstrip("/") + "/transmission/rpc"
    req = urllib.request.Request(rpc_url, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    if user:
        creds = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
    try:
        urllib.request.urlopen(req, timeout=8)
        return ""  # 200 sans session-id (rare)
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return exc.headers.get("X-Transmission-Session-Id", "")
        raise RuntimeError(f"Transmission HTTP {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"Impossible de joindre Transmission : {exc}") from exc


def _send_torrent(torrent_path: Path, base_url: str, user: str, pwd: str, dest_dir: str) -> dict:
    """Envoie un fichier .torrent à Transmission via JSON-RPC."""
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
# Dashboard HTML (inline)
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Torrent Scraper Dashboard</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f0f14; color: #e2e2e8; min-height: 100vh; }

    /* Header + tabs */
    header { background: #16161f; border-bottom: 1px solid #2a2a38; padding: .75rem 1.5rem; display: flex; align-items: center; gap: 2rem; position: sticky; top: 0; z-index: 100; }
    header h1 { font-size: 1.2rem; color: #a78bfa; white-space: nowrap; }
    .tabs { display: flex; gap: .25rem; }
    .tab-btn { background: none; border: none; color: #6b7280; padding: .5rem 1rem; border-radius: 6px; cursor: pointer; font-size: .9rem; font-weight: 600; transition: all .15s; }
    .tab-btn:hover { background: #1c1c27; color: #c4c4d4; }
    .tab-btn.active { background: #2e1e6b; color: #a78bfa; }

    /* Panels */
    .tab-pane { display: none; padding: 1.5rem; max-width: 1000px; margin: 0 auto; }
    .tab-pane.active { display: block; }

    /* Forms */
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: .75rem 1.5rem; margin-bottom: 1rem; }
    .form-group { display: flex; flex-direction: column; gap: .3rem; }
    .form-group label { font-size: .8rem; color: #6b7280; font-weight: 600; }
    .form-group input, .form-group select { background: #1c1c27; border: 1px solid #2a2a38; color: #e2e2e8; padding: .45rem .7rem; border-radius: 6px; font-size: .9rem; width: 100%; }
    .form-group input:focus, .form-group select:focus { outline: none; border-color: #7c3aed; }
    .form-group.full { grid-column: 1 / -1; }
    .checkbox-row { display: flex; align-items: center; gap: .5rem; margin-bottom: 1rem; }
    .checkbox-row input { width: auto; accent-color: #7c3aed; }
    .checkbox-row label { font-size: .9rem; color: #c4c4d4; }

    /* Buttons */
    .btn { display: inline-flex; align-items: center; gap: .4rem; padding: .55rem 1.2rem; border-radius: 8px; border: none; cursor: pointer; font-size: .9rem; font-weight: 600; transition: background .15s; }
    .btn-primary { background: #7c3aed; color: #fff; }
    .btn-primary:hover { background: #6d28d9; }
    .btn-secondary { background: #2a2a38; color: #c4c4d4; }
    .btn-secondary:hover { background: #3a3a50; }
    .btn-danger { background: #7f1d1d; color: #fca5a5; }
    .btn-danger:hover { background: #991b1b; }
    .btn:disabled { opacity: .5; cursor: not-allowed; }

    /* Section titles */
    h2 { font-size: 1rem; color: #a78bfa; margin-bottom: 1rem; padding-bottom: .5rem; border-bottom: 1px solid #2a2a38; }
    .section { margin-bottom: 2rem; }

    /* Progress log */
    #progress-log { background: #0a0a0f; border: 1px solid #2a2a38; border-radius: 8px; padding: .75rem; min-height: 120px; max-height: 320px; overflow-y: auto; font-family: monospace; font-size: .82rem; margin-top: 1rem; }
    .log-line { padding: .15rem 0; border-bottom: 1px solid #16161f; display: flex; gap: .6rem; align-items: baseline; }
    .log-line:last-child { border-bottom: none; }
    .log-ok    { color: #6ee7b7; }
    .log-err   { color: #fca5a5; }
    .log-info  { color: #93c5fd; }
    .log-dim   { color: #4b5563; }

    /* Report selector */
    .report-bar { display: flex; gap: .75rem; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; }
    .report-bar select { flex: 1; min-width: 200px; background: #1c1c27; border: 1px solid #2a2a38; color: #e2e2e8; padding: .45rem .7rem; border-radius: 6px; font-size: .9rem; }

    /* Films iframe */
    #films-frame { width: 100%; height: calc(100vh - 200px); border: 1px solid #2a2a38; border-radius: 8px; background: #0f0f14; }

    /* Transmission — selected films list */
    #trans-films-list { margin-top: 1rem; display: flex; flex-direction: column; gap: .4rem; max-height: 320px; overflow-y: auto; }
    .trans-film { display: flex; justify-content: space-between; align-items: center; background: #1c1c27; border: 1px solid #2a2a38; border-radius: 6px; padding: .5rem .8rem; font-size: .85rem; }
    .trans-film.sent  { border-color: #064e3b; background: #052e1a; }
    .trans-film.error { border-color: #7f1d1d; background: #2a0a0a; }
    .trans-film.trans-film-missing { border-color: #78350f; background: #1c120a; opacity: .7; }
    .trans-film span.name { color: #f3f3f8; }
    .trans-film span.status { font-size: .75rem; color: #6b7280; }

    /* Toast */
    #toast { position: fixed; bottom: 1.5rem; right: 1.5rem; padding: .6rem 1.2rem; border-radius: 8px; font-size: .85rem; font-weight: 600; opacity: 0; transition: opacity .25s; pointer-events: none; z-index: 999; }
    #toast.ok    { background: #064e3b; color: #6ee7b7; }
    #toast.error { background: #7f1d1d; color: #fca5a5; }
    #toast.show  { opacity: 1; }

    /* Badges */
    .badge { display: inline-block; padding: .15rem .55rem; border-radius: 999px; font-size: .75rem; font-weight: 600; }
    .badge-idle    { background: #1c1c27; color: #6b7280; }
    .badge-running { background: #1e3a5f; color: #60a5fa; }
    .badge-done    { background: #064e3b; color: #6ee7b7; }

    @media (max-width: 600px) {
      .form-grid { grid-template-columns: 1fr; }
      header { flex-direction: column; align-items: flex-start; gap: .5rem; }
    }
  </style>
</head>
<body>
<header>
  <h1>🎬 Torrent Scraper</h1>
  <nav class="tabs">
    <button class="tab-btn active" data-tab="scraping">⚙️ Scraping</button>
    <button class="tab-btn" data-tab="films">🎥 Films</button>
    <button class="tab-btn" data-tab="transmission">📡 Transmission</button>
  </nav>
  <span id="job-badge" class="badge badge-idle">Inactif</span>
</header>

<!-- ═══════════════════════ TAB 1 — SCRAPING ═══════════════════════ -->
<section id="tab-scraping" class="tab-pane active">
  <div class="section">
    <h2>Paramètres</h2>
    <div class="form-grid">
      <div class="form-group full">
        <label>URL cible</label>
        <input id="p-base-url" type="url">
      </div>
      <div class="form-group">
        <label>Nb max de films</label>
        <input id="p-max-films" type="number" min="1">
      </div>
      <div class="form-group">
        <label>Taille max (Go)</label>
        <input id="p-max-size" type="number" step="0.5" min="0.5">
      </div>
      <div class="form-group">
        <label>Années recherchées</label>
        <input id="p-years" type="text" placeholder="2025,2026">
      </div>
      <div class="form-group">
        <label>Dossier de sortie</label>
        <input id="p-output-dir" type="text">
      </div>
      <div class="form-group">
        <label>URL Rico</label>
        <input id="p-rico-url" type="url">
      </div>
      <div class="form-group">
        <label>TMDB Bearer Token</label>
        <input id="p-tmdb-token" type="password">
      </div>
      <div class="form-group">
        <label>TMDB API Key</label>
        <input id="p-tmdb-key" type="text">
      </div>
    </div>
    <div class="checkbox-row">
      <input id="p-headless" type="checkbox">
      <label for="p-headless">Mode headless (sans fenêtre navigateur)</label>
    </div>
    <button id="btn-start" class="btn btn-primary">▶ Lancer le scraping</button>
    <button id="btn-stop" class="btn btn-danger" style="display:none">⏹ Arrêter</button>
  </div>

  <div class="section">
    <h2>Progression</h2>
    <div id="progress-log"><span class="log-dim">En attente du lancement…</span></div>
  </div>
</section>

<!-- ═══════════════════════ TAB 2 — FILMS ═══════════════════════ -->
<section id="tab-films" class="tab-pane">
  <div class="report-bar">
    <select id="report-select"><option value="">— Sélectionner un rapport —</option></select>
    <button id="btn-load-report" class="btn btn-primary">📂 Ouvrir</button>
    <button id="btn-refresh-reports" class="btn btn-secondary">↺ Actualiser</button>
  </div>
  <iframe id="films-frame" src="about:blank" title="Rapport films"></iframe>
</section>

<!-- ═══════════════════════ TAB 3 — TRANSMISSION ═══════════════════════ -->
<section id="tab-transmission" class="tab-pane">
  <div class="section">
    <h2>Connexion Transmission</h2>
    <div class="form-grid">
      <div class="form-group full">
        <label>URL du serveur</label>
        <input id="t-url" type="url" placeholder="http://ricohoho.fr:9091">
      </div>
      <div class="form-group">
        <label>Utilisateur</label>
        <input id="t-user" type="text">
      </div>
      <div class="form-group">
        <label>Mot de passe</label>
        <input id="t-pass" type="password">
      </div>
      <div class="form-group full">
        <label>Dossier de destination</label>
        <input id="t-dir" type="text" placeholder="/home/streaming/films">
      </div>
    </div>
    <button id="btn-test-trans" class="btn btn-secondary">🔌 Tester la connexion</button>
  </div>

  <div class="section">
    <h2>Films sélectionnés</h2>
    <button id="btn-refresh-trans" class="btn btn-secondary" style="margin-bottom:.75rem">↺ Actualiser depuis le rapport actif</button>
    <div id="trans-films-list"><span class="log-dim">Aucun film sélectionné.</span></div>
    <br>
    <button id="btn-send-trans" class="btn btn-primary" disabled>📡 Envoyer vers Transmission</button>
  </div>
</section>

<div id="toast"></div>

<script>
// ─── Toast ───────────────────────────────────────────────────────────────────
const toast = document.getElementById('toast');
let _toastTimer = null;
function showToast(msg, type = 'ok') {
  toast.textContent = msg;
  toast.className = type + ' show';
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { toast.className = type; }, 2500);
}

// ─── Tabs ────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'films') loadReports();
    if (btn.dataset.tab === 'transmission') refreshTransFilms();
  });
});

// ─── Init form fields depuis /api/config ─────────────────────────────────────
fetch('/api/config').then(r => r.json()).then(cfg => {
  document.getElementById('p-base-url').value   = cfg.base_url    || '';
  document.getElementById('p-max-films').value  = cfg.max_films   || '';
  document.getElementById('p-max-size').value   = cfg.max_size_gb || '';
  document.getElementById('p-years').value      = cfg.years       || '';
  document.getElementById('p-output-dir').value = cfg.output_dir  || '';
  document.getElementById('p-rico-url').value   = cfg.rico_api_url      || '';
  document.getElementById('p-tmdb-token').value = cfg.tmdb_bearer_token || '';
  document.getElementById('p-tmdb-key').value   = cfg.tmdb_api_key      || '';
  document.getElementById('p-headless').checked = !!cfg.headless;
  document.getElementById('t-url').value        = cfg.transmission_url  || '';
  document.getElementById('t-user').value       = cfg.transmission_user || '';
  document.getElementById('t-pass').value       = cfg.transmission_pass || '';
  document.getElementById('t-dir').value        = cfg.transmission_dir  || '';
}).catch(err => console.error('Chargement config :', err));

// ─── Scraping ────────────────────────────────────────────────────────────────
const logEl   = document.getElementById('progress-log');
const badge   = document.getElementById('job-badge');
const btnStart = document.getElementById('btn-start');
const btnStop  = document.getElementById('btn-stop');

function appendLog(text, cls = 'log-info') {
  const line = document.createElement('div');
  line.className = 'log-line ' + cls;
  line.textContent = text;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function setJobRunning(running) {
  btnStart.disabled = running;
  btnStop.style.display = running ? 'inline-flex' : 'none';
  badge.className = 'badge ' + (running ? 'badge-running' : 'badge-idle');
  badge.textContent = running ? 'En cours…' : 'Inactif';
}

let _evtSource = null;

btnStart.addEventListener('click', async () => {
  logEl.innerHTML = '';
  setJobRunning(true);
  appendLog('Démarrage du scraping…', 'log-dim');

  const body = {
    base_url:          document.getElementById('p-base-url').value,
    max_films:         document.getElementById('p-max-films').value,
    years:             document.getElementById('p-years').value,
    max_size_gb:       document.getElementById('p-max-size').value,
    output_dir:        document.getElementById('p-output-dir').value,
    headless:          document.getElementById('p-headless').checked,
    rico_api_url:      document.getElementById('p-rico-url').value,
    tmdb_bearer_token: document.getElementById('p-tmdb-token').value,
    tmdb_api_key:      document.getElementById('p-tmdb-key').value,
  };

  const r = await fetch('/api/scrape/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });

  if (!r.ok) {
    const err = await r.json();
    appendLog('Erreur : ' + err.error, 'log-err');
    setJobRunning(false);
    return;
  }

  if (_evtSource) _evtSource.close();
  _evtSource = new EventSource('/api/scrape/progress');
  _evtSource.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'ping') return;
    if (msg.type === 'browser_launched')  appendLog('Navigateur lancé', 'log-info');
    else if (msg.type === 'films_found')  appendLog(`${msg.count} films trouvés — ${msg.selected} retenus`, 'log-info');
    else if (msg.type === 'report_saved') appendLog('Rapport JSON créé', 'log-dim');
    else if (msg.type === 'torrent_ok')   appendLog(`✓ [${msg.index}/${msg.total}] ${msg.title}  ${msg.size}`, 'log-ok');
    else if (msg.type === 'torrent_error')appendLog(`✗ [${msg.index}/${msg.total}] ${msg.title} — ${msg.error}`, 'log-err');
    else if (msg.type === 'scrape_done')  appendLog(`Scraping terminé — ${msg.downloaded} torrent(s) téléchargé(s)`, 'log-info');
    else if (msg.type === 'enriching')    appendLog(msg.message, 'log-dim');
    else if (msg.type === 'all_done') {
      badge.className = 'badge badge-done';
      badge.textContent = 'Terminé';
      appendLog(`✅ Terminé — rapport : ${msg.report}`, 'log-ok');
      showToast('Scraping + enrichissement terminés !');
      setJobRunning(false);
      _evtSource.close();
      _lastReport = msg.report;
    } else if (msg.type === 'error') {
      appendLog('Erreur : ' + msg.message, 'log-err');
      setJobRunning(false);
      _evtSource.close();
    }
  };
  _evtSource.onerror = () => {
    if (_evtSource.readyState === EventSource.CLOSED) return;
    appendLog('Connexion SSE interrompue', 'log-err');
    setJobRunning(false);
  };
});

// ─── Films ───────────────────────────────────────────────────────────────────
const reportSel = document.getElementById('report-select');
const filmsFrame = document.getElementById('films-frame');
let _lastReport = null;

async function loadReports() {
  const r = await fetch('/api/reports');
  const data = await r.json();
  reportSel.innerHTML = '<option value="">— Sélectionner un rapport —</option>';
  data.forEach(f => {
    const opt = document.createElement('option');
    opt.value = f.name;
    opt.textContent = f.name + (f.active ? ' ✦' : '');
    if (f.active) opt.selected = true;
    reportSel.appendChild(opt);
  });
  if (_lastReport) {
    reportSel.value = _lastReport;
  }
}

document.getElementById('btn-load-report').addEventListener('click', () => {
  const name = reportSel.value;
  if (!name) { showToast('Sélectionnez un rapport', 'error'); return; }
  filmsFrame.src = '/films/' + encodeURIComponent(name);
  _lastReport = name;
});

document.getElementById('btn-refresh-reports').addEventListener('click', loadReports);

// ─── Transmission ────────────────────────────────────────────────────────────
const transListEl = document.getElementById('trans-films-list');
const btnSendTrans = document.getElementById('btn-send-trans');
let _transFilms = [];

async function refreshTransFilms() {
  const r = await fetch('/api/reports');
  const reports = await r.json();
  const active = reports.find(f => f.active);
  if (!active) {
    transListEl.innerHTML = `<span class="log-dim">Aucun rapport actif. Ouvrez un rapport dans l'onglet Films.</span>`;
    btnSendTrans.disabled = true;
    _transFilms = [];
    return;
  }
  // Lire le JSON directement via /films/<name> retourne du HTML — on va chercher le JSON via une autre route
  const cfgR = await fetch('/api/report-json/' + encodeURIComponent(active.name));
  if (!cfgR.ok) { _transFilms = []; return; }
  const data = await cfgR.json();
  _transFilms = (data.Fichiers || []).filter(f => f.selectionne && f.torrent_ok);
  const sans_torrent = (data.Fichiers || []).filter(f => f.selectionne && !f.torrent_ok);
  renderTransFilms(sans_torrent);
}

function renderTransFilms(sans_torrent = []) {
  transListEl.innerHTML = '';
  sans_torrent.forEach(f => {
    const div = document.createElement('div');
    div.className = 'trans-film trans-film-missing';
    div.innerHTML = `<span class="name">${f.film || f.Fichier} (${f.annee || '?'})</span><span class="status" style="color:#f87171">⚠ .torrent non téléchargé</span>`;
    transListEl.appendChild(div);
  });
  if (!_transFilms.length) {
    if (!sans_torrent.length) transListEl.innerHTML = '<span class="log-dim">Aucun film sélectionné dans le rapport actif.</span>';
    btnSendTrans.disabled = true;
    return;
  }
  _transFilms.forEach(f => {
    const div = document.createElement('div');
    div.className = 'trans-film';
    div.dataset.film = f.film || f.Fichier;
    div.innerHTML = `<span class="name">${f.film || f.Fichier} (${f.annee || '?'})</span><span class="status">${f.Taille || ''}</span>`;
    transListEl.appendChild(div);
  });
  btnSendTrans.disabled = false;
}

document.getElementById('btn-refresh-trans').addEventListener('click', refreshTransFilms);

document.getElementById('btn-test-trans').addEventListener('click', async () => {
  const params = new URLSearchParams({
    url:  document.getElementById('t-url').value,
    user: document.getElementById('t-user').value,
    pass: document.getElementById('t-pass').value,
  });
  const r = await fetch('/api/transmission/test?' + params);
  const d = await r.json();
  showToast(d.message, d.ok ? 'ok' : 'error');
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
  const r = await fetch('/api/transmission/send', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  const data = await r.json();
  data.results.forEach(res => {
    const el = transListEl.querySelector(`[data-film="${CSS.escape(res.film)}"]`);
    if (el) {
      el.classList.add(res.ok ? 'sent' : 'error');
      el.querySelector('.status').textContent = res.ok ? '✓ Envoyé' : '✗ ' + res.error;
    }
  });
  const ok = data.results.filter(r => r.ok).length;
  showToast(`${ok}/${data.results.length} torrent(s) envoyé(s)`, ok === data.results.length ? 'ok' : 'error');
  btnSendTrans.disabled = false;
});
</script>
</body>
</html>"""
