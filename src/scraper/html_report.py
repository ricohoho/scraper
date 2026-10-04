"""Génération d'une page HTML de présentation du rapport JSON enrichi."""

from __future__ import annotations

import html
import json
import urllib.parse
from pathlib import Path


def _card(item: dict) -> str:
    fichier = html.escape(item.get("Fichier", ""))
    film    = html.escape(item.get("film", fichier))
    annee   = html.escape(item.get("annee", ""))
    taille  = html.escape(item.get("Taille", ""))
    rico    = item.get("ricofilm", "")
    absent  = rico == "absent"
    torrent_ok = item.get("torrent_ok")  # None = pas vérifié (HTML statique)

    status_cls   = "badge-absent" if absent else "badge-trouve"
    status_label = "Absent de Rico" if absent else "Présent sur Rico"
    torrent_badge = (
        '<span class="badge badge-torrent-missing">⚠ Torrent non téléchargé</span>'
        if torrent_ok is False else ""
    )

    # --- Checkbox de sélection (inerte sans JS dans la page statique) ---
    checked = "checked" if item.get("selectionne") else ""
    fichier_attr = html.escape(item.get("Fichier", ""), quote=True)
    checkbox_html = (
        f'<label class="select-label">'
        f'<input type="checkbox" class="film-select" data-fichier="{fichier_attr}" {checked}>'
        f' Sélectionner</label>'
    )

    # --- Affiche ---
    affiche_url = item.get("affiche", "")
    if affiche_url:
        poster_html = f'<img class="poster" src="{html.escape(affiche_url)}" alt="Affiche {film}" loading="lazy">'
    else:
        poster_html = '<div class="poster poster-placeholder">🎬</div>'

    # --- Lien TMDB ---
    film_raw = item.get("film", item.get("Fichier", ""))
    tmdb_url = item.get("tmdb_url", "")
    if not tmdb_url:
        tmdb_id = item.get("tmdb_id")
        if tmdb_id:
            tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}"
        elif film_raw:
            tmdb_url = f"https://www.themoviedb.org/search?query={urllib.parse.quote(film_raw)}"
        else:
            tmdb_url = "https://www.themoviedb.org/"

    tmdb_html = (
        f'<a class="btn-tmdb" href="{html.escape(tmdb_url)}" '
        f'target="_blank" rel="noopener">🎬 Fiche TMDB</a>'
    )

    # --- Bande annonce ---
    annonce_url = item.get("url_annonce", "")
    if annonce_url:
        annonce_html = (
            f'<a class="btn-trailer" href="{html.escape(annonce_url)}" '
            f'target="_blank" rel="noopener">▶ Bande-annonce</a>'
        )
    else:
        annonce_html = ""

    # --- Résumé ---
    resume = html.escape(item.get("résume", ""))
    resume_html = f'<p class="resume">{resume}</p>' if resume else ""

    # --- Acteurs ---
    acteurs = item.get("acteurs", [])
    acteurs_html = ""
    if acteurs:
        tags = "".join(f'<span class="tag">{html.escape(a)}</span>' for a in acteurs)
        acteurs_html = f'<div class="row-meta"><span class="label">Acteurs</span>{tags}</div>'

    # --- Réalisateur ---
    real = html.escape(item.get("metteur_en_scene", ""))
    real_html = (
        f'<div class="row-meta"><span class="label">Réalisateur</span>'
        f'<span class="tag">{real}</span></div>'
    ) if real else ""

    # --- Fichier technique ---
    tech_html = (
        f'<div class="row-meta fichier-tech">'
        f'<span class="label">Fichier</span>'
        f'<span class="filename">{fichier}</span>'
        f'</div>'
    )

    selected_cls = " card-selected" if item.get("selectionne") else ""

    return f"""
    <div class="card {'card-absent' if absent else 'card-trouve'}{selected_cls}">
      <div class="card-poster">{poster_html}</div>
      <div class="card-body">
        <div class="card-header">
          <div class="header-top">
            <h2 class="title">{film}</h2>
            {checkbox_html}
          </div>
          <div class="meta-line">
            <span class="annee">{annee}</span>
            <span class="taille">{taille}</span>
            <span class="badge {status_cls}">{status_label}</span>
            {torrent_badge}
          </div>
        </div>
        {resume_html}
        {real_html}
        {acteurs_html}
        <div class="card-footer">
          <div class="btn-row">
            {tmdb_html}
            {annonce_html}
          </div>
          {tech_html}
        </div>
      </div>
    </div>"""


_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: 'Segoe UI', system-ui, sans-serif;
  background: #0f0f14;
  color: #e2e2e8;
  padding: 2rem 1rem;
}

header {
  text-align: center;
  margin-bottom: 2rem;
}
header h1 { font-size: 1.8rem; color: #a78bfa; }
header p  { color: #6b7280; font-size: 0.9rem; margin-top: .3rem; }

.stats {
  display: flex;
  justify-content: center;
  gap: 1.5rem;
  margin-bottom: 2rem;
  flex-wrap: wrap;
}
.stat { background: #1c1c27; border-radius: 8px; padding: .6rem 1.2rem; text-align: center; }
.stat strong { display: block; font-size: 1.5rem; color: #a78bfa; }
.stat span   { font-size: .8rem; color: #6b7280; }

.grid {
  display: grid;
  gap: 1.2rem;
  max-width: 1100px;
  margin: 0 auto;
}

.card {
  display: flex;
  gap: 1rem;
  background: #1c1c27;
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid #2a2a38;
  transition: border-color .2s;
}
.card:hover { border-color: #7c3aed; }
.card-absent { border-left: 4px solid #f59e0b; }
.card-trouve { border-left: 4px solid #10b981; }

.card-poster {
  flex: 0 0 auto;
  width: 110px;
}
.poster {
  width: 110px;
  height: 165px;
  object-fit: cover;
  display: block;
}
.poster-placeholder {
  width: 110px;
  height: 165px;
  background: #2a2a38;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 2.5rem;
  color: #4b4b6a;
}

.card-body {
  flex: 1;
  padding: .9rem 1rem .9rem 0;
  display: flex;
  flex-direction: column;
  gap: .5rem;
  min-width: 0;
}

.card-header { display: flex; flex-direction: column; gap: .25rem; }

.title { font-size: 1.15rem; font-weight: 700; color: #f3f3f8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.meta-line { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
.annee  { color: #9ca3af; font-size: .85rem; }
.taille { color: #9ca3af; font-size: .85rem; }

.badge {
  display: inline-block;
  padding: .15rem .55rem;
  border-radius: 999px;
  font-size: .75rem;
  font-weight: 600;
}
.badge-trouve { background: #064e3b; color: #6ee7b7; }
.badge-absent { background: #451a03; color: #fcd34d; }
.badge-torrent-missing { background: #3b1f1f; color: #f87171; }

.resume {
  font-size: .85rem;
  color: #c4c4d4;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.row-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: .35rem;
  font-size: .8rem;
}
.label {
  color: #6b7280;
  font-weight: 600;
  white-space: nowrap;
  margin-right: .15rem;
}
.tag {
  background: #2a2a3e;
  border-radius: 4px;
  padding: .1rem .45rem;
  color: #c4c4d4;
}

.card-footer {
  margin-top: auto;
  display: flex;
  align-items: flex-start;
  flex-direction: column;
  gap: .5rem;
}

.btn-row {
  display: flex;
  align-items: center;
  gap: .5rem;
  flex-wrap: wrap;
}

.btn-trailer {
  display: inline-block;
  background: #7c3aed;
  color: #fff;
  text-decoration: none;
  padding: .35rem .9rem;
  border-radius: 6px;
  font-size: .82rem;
  font-weight: 600;
  transition: background .15s;
}
.btn-trailer:hover { background: #6d28d9; }

.btn-tmdb {
  display: inline-block;
  background: #01b4e4;
  color: #0d253f;
  text-decoration: none;
  padding: .35rem .9rem;
  border-radius: 6px;
  font-size: .82rem;
  font-weight: 700;
  transition: background .15s, color .15s;
}
.btn-tmdb:hover { background: #0096c7; color: #ffffff; }

.fichier-tech { opacity: .55; font-size: .75rem; }
.filename { word-break: break-all; color: #8b8baa; }

@media (max-width: 500px) {
  .card { flex-direction: column; }
  .card-poster, .poster, .poster-placeholder { width: 100%; height: 180px; }
  .card-body { padding: .8rem; }
}
"""


_CSS_INTERACTIVE = """
.header-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: .5rem;
}
.select-label {
  display: inline-flex;
  align-items: center;
  gap: .35rem;
  cursor: pointer;
  font-size: .8rem;
  color: #a78bfa;
  white-space: nowrap;
  user-select: none;
  flex-shrink: 0;
}
.film-select {
  accent-color: #7c3aed;
  width: 1rem;
  height: 1rem;
  cursor: pointer;
}
.card-selected {
  border-color: #7c3aed !important;
  background: #1e1830;
}
#toast {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  padding: .6rem 1.2rem;
  border-radius: 8px;
  font-size: .85rem;
  font-weight: 600;
  opacity: 0;
  transition: opacity .25s;
  pointer-events: none;
  z-index: 999;
}
#toast.ok    { background: #064e3b; color: #6ee7b7; }
#toast.error { background: #7f1d1d; color: #fca5a5; }
#toast.show  { opacity: 1; }
.stat-select strong { color: #7c3aed; }
"""

_JS = """
(function () {
  // --- Toast ---
  const toast = document.createElement('div');
  toast.id = 'toast';
  document.body.appendChild(toast);
  let _toastTimer = null;

  function showToast(msg, type) {
    toast.textContent = msg;
    toast.className = type + ' show';
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => { toast.className = type; }, 2000);
  }

  function getToken() {
    try {
      if (window.parent && window.parent.localStorage) {
        const t = window.parent.localStorage.getItem('torrent_scraper_token');
        if (t) return t;
      }
    } catch (e) {}
    return new URLSearchParams(window.location.search).get('token') || '';
  }

  // --- Compteur sélectionnés ---
  const statEl = document.querySelector('.stat-select strong');
  function updateCount() {
    if (!statEl) return;
    statEl.textContent = document.querySelectorAll('.film-select:checked').length;
  }
  updateCount();

  // --- Checkboxes ---
  document.querySelectorAll('.film-select').forEach(function (cb) {
    cb.addEventListener('change', async function () {
      const fichier = cb.dataset.fichier;
      const selectionne = cb.checked;
      const card = cb.closest('.card');
      card.classList.toggle('card-selected', selectionne);
      updateCount();

      try {
        const headers = { 'Content-Type': 'application/json' };
        const token = getToken();
        if (token) {
          headers['Authorization'] = 'Bearer ' + token;
        }
        const r = await fetch('/select', {
          method: 'POST',
          headers: headers,
          body: JSON.stringify({ fichier: fichier, selectionne: selectionne }),
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        showToast(selectionne ? 'Sélectionné ✓' : 'Désélectionné', 'ok');
      } catch (err) {
        // Annulation visuelle si le serveur ne répond pas.
        cb.checked = !selectionne;
        card.classList.toggle('card-selected', !selectionne);
        updateCount();
        showToast('Erreur : ' + err.message, 'error');
      }
    });
  });
})();
"""


def _build_page(data: dict, interactive: bool) -> str:
    """Construit le HTML complet. `interactive=True` ajoute CSS + JS pour le serveur."""
    fichiers: list[dict] = data.get("Fichiers", [])
    site  = html.escape(data.get("site", ""))
    date  = html.escape(data.get("date", ""))

    total    = len(fichiers)
    absents  = sum(1 for f in fichiers if f.get("ricofilm") == "absent")
    trouves  = total - absents
    selected = sum(1 for f in fichiers if f.get("selectionne"))

    cards = "\n".join(_card(item) for item in fichiers)

    extra_css    = _CSS_INTERACTIVE if interactive else ""
    extra_script = f"<script>{_JS}</script>" if interactive else ""
    stat_select  = (
        f'<div class="stat stat-select"><strong>{selected}</strong>'
        f'<span>sélectionnés</span></div>'
    ) if interactive else ""

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Films scrappés — {date}</title>
  <style>{_CSS}{extra_css}</style>
</head>
<body>
  <header>
    <h1>Films scrappés</h1>
    <p>{site} &mdash; {date}</p>
  </header>
  <div class="stats">
    <div class="stat"><strong>{total}</strong><span>films analysés</span></div>
    <div class="stat"><strong>{trouves}</strong><span>présents sur Rico</span></div>
    <div class="stat"><strong>{absents}</strong><span>absents de Rico</span></div>
    {stat_select}
  </div>
  <div class="grid">
{cards}
  </div>
  {extra_script}
</body>
</html>"""


def serve_html(data: dict) -> str:
    """Retourne le HTML interactif (avec checkboxes + JS) pour le serveur local."""
    return _build_page(data, interactive=True)


def save_html_report(json_path: Path) -> Path:
    """Génère <nom_json>.html (page statique, sans JS) dans le même dossier que le JSON."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    dest = json_path.with_suffix(".html")
    dest.write_text(_build_page(data, interactive=False), encoding="utf-8")
    return dest
