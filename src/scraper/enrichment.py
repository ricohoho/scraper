"""Enrichissement du rapport JSON : extraction titre/année, Rico, TMDB."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.error import URLError

from .config import Settings
from .html_report import save_html_report

logger = logging.getLogger(__name__)

# Année entre 1900 et 2099, non précédée/suivie d'un chiffre.
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

_TMDB_BASE = "https://api.themoviedb.org/3"
_YOUTUBE_URL = "https://www.youtube.com/watch?v="
_TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w500"


# ---------------------------------------------------------------------------
# Parsing du nom de fichier
# ---------------------------------------------------------------------------

def _clean_name(raw: str) -> str:
    """Remplace les points par des espaces si aucun espace n'est présent."""
    if "." in raw and " " not in raw.strip():
        return raw.rstrip(".").replace(".", " ").strip()
    return raw.strip()


def parse_film_info(fichier: str) -> tuple[str, str | None]:
    """Extrait (titre_propre, année) depuis un nom de fichier torrent.

    Gère indifféremment les séparateurs espaces et points.
    Ex. « Warrior.2011.MULTi.1080p… »         → (« Warrior », « 2011 »)
    Ex. « Los Domingos 2025 MULTi… »           → (« Los Domingos », « 2025 »)
    Ex. « 2001.A.Space.Odyssey.1968.1080p… »   → (« 2001 A Space Odyssey », « 1968 »)
    """
    matches = list(_YEAR_RE.finditer(fichier))
    if not matches:
        name = fichier.replace(".", " ") if " " not in fichier else fichier
        return name.strip(), None

    # Si le premier match laisse un titre vide (l'année EST au début du nom),
    # on prend le second match comme séparateur titre/année.
    for match in matches:
        before = fichier[: match.start()]
        name = _clean_name(before)
        if name:
            return name, match.group(1)

    # Aucun match ne laisse de titre : on retourne le fichier tel quel sans année.
    return _clean_name(fichier), None


# ---------------------------------------------------------------------------
# Webservice Rico
# ---------------------------------------------------------------------------

def _http_get(url: str, params: dict, headers: dict, timeout: int = 8) -> object:
    """GET HTTP simple via urllib (stdlib). Renvoie l'objet JSON décodé, ou None."""
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("HTTP GET %s : %s", full_url, exc)
        return None


def check_rico(film: str, year: str | None, base_url: str) -> bool:
    """Renvoie True si au moins un résultat est retourné par le webservice local."""
    params: dict = {
        "titles": film,
        "skip": 0,
        "limit": 20,
        "sort": "original_title",
        "sortsens": 1,
    }
    if year:
        params["release_year"] = year

    data = _http_get(f"{base_url}/films/list", params, {"accept": "application/json"})
    if data is None:
        return False
    if isinstance(data, list):
        return len(data) > 0
    if isinstance(data, dict):
        for key in ("results", "items", "data", "films"):
            if data.get(key):
                return True
        return bool(data.get("total") or data.get("count"))
    return False


# ---------------------------------------------------------------------------
# TMDB
# ---------------------------------------------------------------------------

def _tmdb_headers(settings: Settings) -> dict:
    h = {"accept": "application/json"}
    if settings.tmdb_bearer_token:
        h["Authorization"] = f"Bearer {settings.tmdb_bearer_token}"
    return h


def _tmdb_base_params(settings: Settings) -> dict:
    if settings.tmdb_api_key and not settings.tmdb_bearer_token:
        return {"api_key": settings.tmdb_api_key}
    return {}


def _find_trailer(videos: list[dict]) -> str:
    """Cherche une bande-annonce YouTube : français d'abord, puis toute langue."""
    for lang_filter in ("fr", None):
        for v in videos:
            if v.get("site") != "YouTube" or v.get("type") != "Trailer":
                continue
            if lang_filter and v.get("iso_639_1") != lang_filter:
                continue
            return f"{_YOUTUBE_URL}{v['key']}"
    return ""


def fetch_tmdb_info(film: str, year: str | None, settings: Settings) -> dict:
    """Cherche le film sur TMDB et renvoie un dict avec résumé, acteurs, réalisateur, annonce."""
    headers = _tmdb_headers(settings)
    base_params = _tmdb_base_params(settings)

    if not settings.tmdb_bearer_token and not settings.tmdb_api_key:
        logger.warning("Aucune clé TMDB configurée (SCRAPER_TMDB_BEARER_TOKEN ou SCRAPER_TMDB_API_KEY).")
        return {}

    # 1. Recherche du film.
    search_params = {**base_params, "query": film, "language": "fr-FR"}
    if year:
        search_params["year"] = year

    search_data = _http_get(f"{_TMDB_BASE}/search/movie", search_params, headers)
    results = (search_data or {}).get("results", []) if isinstance(search_data, dict) else []

    if not results:
        logger.info("TMDB : aucun résultat pour « %s » (%s)", film, year)
        return {}

    movie_id = results[0]["id"]

    # 2. Détails + crédits + vidéos en un seul appel.
    detail_params = {**base_params, "language": "fr-FR", "append_to_response": "credits,videos"}
    detail = _http_get(f"{_TMDB_BASE}/movie/{movie_id}", detail_params, headers)
    if not isinstance(detail, dict):
        return {}

    cast = detail.get("credits", {}).get("cast", [])
    crew = detail.get("credits", {}).get("crew", [])
    videos = detail.get("videos", {}).get("results", [])

    directors = [m["name"] for m in crew if m.get("job") == "Director"]

    poster_path = detail.get("poster_path") or ""
    affiche = f"{_TMDB_IMG_BASE}{poster_path}" if poster_path else ""

    return {
        "résume": detail.get("overview", ""),
        "acteurs": [m["name"] for m in cast[:5]],
        "metteur_en_scene": directors[0] if directors else "",
        "url_annonce": _find_trailer(videos),
        "affiche": affiche,
    }


# ---------------------------------------------------------------------------
# Entrée principale
# ---------------------------------------------------------------------------

def enrich_report(json_path: Path, settings: Settings) -> None:
    """Lit le rapport JSON, enrichit chaque film en place, réécrit le fichier."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    fichiers: list[dict] = data.get("Fichiers", [])

    total = len(fichiers)
    for i, item in enumerate(fichiers, 1):
        nom = item.get("Fichier", "")
        film_name, year = parse_film_info(nom)
        item["film"] = film_name
        item["annee"] = year or ""

        logger.info("[%d/%d] %s (%s)", i, total, film_name, year or "?")

        found = check_rico(film_name, year, settings.rico_api_url)
        item["ricofilm"] = "trouve" if found else "absent"

        if not found:
            tmdb = fetch_tmdb_info(film_name, year, settings)
            item.update(tmdb if tmdb else {
                "résume": "",
                "acteurs": [],
                "metteur_en_scene": "",
                "url_annonce": "",
                "affiche": "",
            })

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Fichier enrichi : %s", json_path.name)

    html_path = save_html_report(json_path)
    logger.info("Page HTML générée : %s", html_path.name)
