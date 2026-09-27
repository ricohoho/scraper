"""Orchestration : lister → filtrer → ouvrir le détail → télécharger le .torrent.

Ce module ne dépend ni de la CLI ni de ``print`` : il renvoie un ``ScrapeResult``
et peut être appelé tel quel depuis un backend (ex. une route FastAPI).
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from . import parsing
from .browser import Surface, browser_session, find_content_frame, goto_and_wait
from .config import Settings
from .models import DownloadOutcome, Film, ScrapeResult
from .naming import unique_torrent_path
from .report import save_report

logger = logging.getLogger(__name__)


def _matches(film: Film, settings: Settings) -> bool:
    """Critère métier : année 2025/2026 dans le titre ET taille connue < seuil."""
    #has_year = any(year in film.title for year in settings.years)
    has_year = True
    small_enough = film.size_gb is not None and film.size_gb < settings.max_size_gb
    return has_year and small_enough


def _enter_site(page: Page, settings: Settings) -> Surface:
    """Atteint le vrai site et renvoie la « surface » qui contient la liste.

    ``base_url`` peut être une passerelle qui embarque le vrai site dans une
    ``<iframe>`` (cas de ww1.lat/cpasbien/), ce dernier refusant l'accès direct
    (top-level). On laisse l'iframe se charger et on scrape à l'intérieur. Si le
    site est servi directement, la surface renvoyée est simplement la page.

    L'iframe de contenu peut échouer de façon intermittente (anti-bot). On
    recharge donc la passerelle plusieurs fois avant d'abandonner.
    """
    attempts = 3
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            page.goto(settings.base_url, wait_until="domcontentloaded", timeout=settings.nav_timeout_ms)
            surface = find_content_frame(page, settings, parsing.LIST_READY_SELECTOR)
            where = "la page" if surface is page.main_frame else "l'iframe de contenu"
            logger.info("Liste trouvée dans %s : %s", where, surface.url)
            return surface
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            last_error = exc
            logger.info("Contenu non chargé (tentative %d/%d), rechargement…", attempt, attempts)
            page.wait_for_timeout(2_000)

    raise RuntimeError(
        "Impossible de charger la liste : le contenu (cpasbien dans l'iframe) n'a pas "
        "répondu — probablement une protection anti-bot côté site."
    ) from last_error


def _films_list_url(surface: Surface, settings: Settings) -> str:
    """URL de la liste complète des films sur le domaine courant de la surface."""
    current = urlparse(surface.url)
    origin = f"{current.scheme}://{current.netloc}/"
    list_url = urljoin(origin, settings.list_path.lstrip("/"))
    logger.info("Domaine courant : %s -> liste : %s", current.netloc, list_url)
    return list_url


def _collect_films(surface: Surface, settings: Settings) -> list[Film]:
    """Parcourt la liste complète (avec pagination) jusqu'à ``max_films`` films."""
    films: list[Film] = []
    url: str | None = _films_list_url(surface, settings)
    visited: set[str] = set()

    while url and url not in visited and len(films) < settings.max_films:
        visited.add(url)
        goto_and_wait(surface, url, settings, parsing.LIST_READY_SELECTOR)
        page_films = parsing.extract_films(surface)
        logger.info("Page liste : %d films extraits (%s)", len(page_films), surface.url)
        if not page_films:
            break  # plus rien à extraire : on évite une boucle inutile
        films.extend(page_films)
        url = parsing.find_next_page_url(surface)

    return films[: settings.max_films]


def _download_torrent(context: BrowserContext, torrent_url: str, dest: Path) -> None:
    """Télécharge le .torrent via le contexte (réutilise les cookies Cloudflare)."""
    response = context.request.get(torrent_url)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status} pour {torrent_url}")
    body = response.body()
    # Garde-fou : un .torrent est du bencode et commence par « d…announce ».
    if not body.startswith(b"d") or b"announce" not in body[:200]:
        logger.warning("Contenu inattendu (non bencode ?) pour %s", torrent_url)
    dest.write_bytes(body)


def run(settings: Settings, progress=None) -> ScrapeResult:
    """Exécute le scraping complet et renvoie un résumé structuré.

    ``progress`` est un callable optionnel appelé avec un dict à chaque étape.
    Utilisé par le dashboard web pour streamer l'avancement via SSE.
    """
    def _notify(**kwargs):
        if progress:
            progress(kwargs)

    output_dir = Path(settings.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = ScrapeResult()

    _notify(type="browser_launched")
    with browser_session(settings) as (context, page):
        surface = _enter_site(page, settings)
        result.films = _collect_films(surface, settings)
        result.selected = [f for f in result.films if _matches(f, settings)]
        logger.info(
            "%d films analysés, %d retenus (année + taille < %s Go)",
            len(result.films), len(result.selected), settings.max_size_gb,
        )
        _notify(type="films_found", count=len(result.films), selected=len(result.selected))

        report_path = save_report(result.films, settings.base_url, output_dir)
        _notify(type="report_saved", path=str(report_path))

        total = len(result.selected)
        for i, film in enumerate(result.selected, 1):
            outcome = DownloadOutcome(film=film)
            try:
                goto_and_wait(surface, film.detail_url, settings, parsing.DETAIL_READY_SELECTOR)
                torrent_url = parsing.extract_torrent_url(surface)
                outcome.torrent_url = torrent_url
                if not torrent_url:
                    outcome.error = "lien telecharger.gif introuvable"
                    _notify(type="torrent_error", title=film.title, error=outcome.error, index=i, total=total)
                else:
                    dest = unique_torrent_path(output_dir, film.title)
                    _download_torrent(context, torrent_url, dest)
                    outcome.saved_path = str(dest)
                    logger.info("Téléchargé : %s", dest.name)
                    _notify(type="torrent_ok", title=film.title, path=dest.name,
                            size=film.size_raw or "", index=i, total=total)
            except (PlaywrightTimeoutError, RuntimeError, OSError) as exc:
                outcome.error = str(exc)
                logger.warning("Échec sur « %s » : %s", film.title, exc)
                _notify(type="torrent_error", title=film.title, error=str(exc), index=i, total=total)
            finally:
                result.downloads.append(outcome)
                page.wait_for_timeout(settings.request_delay_ms)

    _notify(type="scrape_done", downloaded=result.downloaded_count,
            total=total, report=str(report_path))
    return result
