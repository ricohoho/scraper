"""Session Playwright et franchissement des challenges Cloudflare."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse

from playwright.sync_api import (
    Error as PlaywrightError,
    Frame,
    Page,
    Route,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

# Page et Frame partagent les mêmes méthodes utiles (goto, wait_for_selector,
# query_selector, title, wait_for_timeout, url) : on peut scraper indifféremment
# l'une ou l'autre. « Surface » désigne ce contexte navigable.
Surface = Page | Frame

from .config import Settings

logger = logging.getLogger(__name__)

# User-agent réaliste : un UA par défaut « HeadlessChrome » est un signal anti-bot évident.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Script injecté avant tout chargement : gomme les signaux d'automatisation les plus
# courants (navigator.webdriver, plugins, langues, objet chrome) que les sites
# anti-bot inspectent. Ne garantit rien face à une protection avancée.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""

# Marqueurs typiques d'une page d'attente Cloudflare.
_CLOUDFLARE_TITLES = ("just a moment", "un instant", "checking your browser", "attention required")
_CLOUDFLARE_SELECTORS = ("#challenge-running", "#cf-challenge-running", "iframe[src*='challenges.cloudflare.com']")

# Domaines tiers autorisés même hors cible : challenge Cloudflare (indispensable
# pour franchir la protection) et CDN courants utilisés pour le rendu.
_ALLOWED_THIRD_PARTY = (
    "cloudflare.com",
    "cloudflareinsights.com",
    "challenges.cloudflare.com",
    "gstatic.com",
    "googleapis.com",
    "jsdelivr.net",
)
# Types de ressources « exécutables » tierces à bloquer : ce sont elles qui
# déclenchent pubs, pop-ups et redirections. On NE bloque PAS les sous-documents
# (sub_frame) : certains sites (ex. passerelle cpasbien) servent leur vrai contenu
# dans une iframe qu'il faut laisser charger. Le contenu utile est du HTML rendu
# côté serveur, lisible sans exécuter le moindre script tiers.
_EXECUTABLE_TYPES = {"script", "xhr", "fetch", "websocket", "eventsource"}


def _registrable_domain(host: str) -> str:
    """Domaine « enregistrable » approximatif (deux derniers labels) : ww1.lat, exemple.tld."""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def allowed_suffixes(settings: Settings) -> tuple[str, ...]:
    """Suffixes de domaines considérés « maison » : base_url + domaines supplémentaires.

    Une cible peut rediriger vers un autre de ses domaines (ex. passerelle
    ww1.lat -> cpasbien.fyi) ; on autorise ces domaines tout en bloquant les ads.
    """
    suffixes = {_registrable_domain(urlparse(settings.base_url).hostname or "")}
    for domain in settings.allowed_domains:
        suffixes.add(_registrable_domain(domain))
    return tuple(s for s in suffixes if s)


def _host_allowed(host: str, suffixes: tuple[str, ...]) -> bool:
    if not host or not suffixes:
        return not suffixes  # pas de restriction si aucun suffixe connu
    return any(host.endswith(s) for s in suffixes)


def _install_ad_guards(context, page: Page, settings: Settings) -> tuple[set[str], object]:
    """Neutralise les pubs/redirections agressives de ce type de site.

    Stratégie : couper le mal à la racine, sans connaître les domaines à l'avance.
    On **suit dynamiquement les domaines par lesquels la page principale navigue
    réellement** (la chaîne de redirections « maison » du site, ex. ww1.lat →
    cpasbien.fyi → cpasbien1.cc → cpasbien2.cc) et on autorise leurs ressources.
    Tout **script / requête / iframe d'un autre domaine** (les régies publicitaires)
    est bloqué : la pub ne s'exécute donc jamais et ne peut ni ouvrir de pop-up ni
    rediriger la page. Les ressources passives tierces (images, CSS, polices) et le
    challenge Cloudflare restent autorisés. Les pop-ups éventuels sont fermés.

    Renvoie ``(site_domains, _)`` : l'ensemble vivant des domaines du site, utilisé
    aussi par la navigation pour savoir si l'on est bien « chez le site ».
    """
    # Graine : domaine de base_url + domaines explicitement autorisés (optionnels).
    site_domains: set[str] = set(allowed_suffixes(settings))

    def _track_navigation(frame) -> None:
        if frame is page.main_frame:
            domain = _registrable_domain(urlparse(frame.url).hostname or "")
            # Ignorer les pseudo-domaines des pages d'erreur (chrome-error://…).
            if domain and "." in domain and domain not in site_domains:
                logger.debug("Domaine du site appris : %s", domain)
                site_domains.add(domain)

    def _router(route: Route) -> None:
        request = route.request
        try:
            host = urlparse(request.url).hostname or ""
            on_site = _host_allowed(host, tuple(site_domains))
            whitelisted = any(host.endswith(d) for d in _ALLOWED_THIRD_PARTY)
            if host and not on_site and not whitelisted:
                # On ne bloque QUE l'exécutable tiers (scripts/xhr/iframes) : c'est
                # ce qui déclenche pubs et redirections. Les documents (chaîne de
                # redirections du site) et les ressources passives passent.
                if request.resource_type in _EXECUTABLE_TYPES:
                    logger.debug("Pub tierce bloquée (%s) : %s", request.resource_type, request.url)
                    route.abort()
                    return
        except Exception:  # ne jamais casser la navigation à cause du filtre
            pass
        route.continue_()

    page.on("framenavigated", _track_navigation)
    context.route("**/*", _router)
    page.on("popup", _close_popup)
    return site_domains, None


def _close_popup(popup: Page) -> None:
    try:
        logger.info("Pop-up publicitaire fermé : %s", popup.url)
        popup.close()
    except Exception:
        pass


@contextmanager
def browser_session(settings: Settings) -> Iterator[tuple]:
    """Ouvre un navigateur Chromium et fournit ``(context, page)``.

    Le contexte est partagé avec le téléchargement des .torrent afin de réutiliser
    le cookie de clearance Cloudflare obtenu pendant la navigation.
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=settings.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            locale="fr-FR",
            user_agent=_USER_AGENT,
            viewport={"width": 1366, "height": 900},
        )
        context.set_default_timeout(settings.nav_timeout_ms)
        context.add_init_script(_STEALTH_JS)
        page = context.new_page()
        _install_ad_guards(context, page, settings)
        try:
            yield context, page
        finally:
            context.close()
            browser.close()


def _looks_like_cloudflare(surface: Surface) -> bool:
    title = (surface.title() or "").strip().lower()
    if any(marker in title for marker in _CLOUDFLARE_TITLES):
        return True
    for selector in _CLOUDFLARE_SELECTORS:
        if surface.query_selector(selector) is not None:
            return True
    return False


def find_content_frame(page: Page, settings: Settings, ready_selector: str) -> Surface:
    """Renvoie la « surface » qui contient réellement la liste de films.

    Certains sites (passerelle cpasbien) servent leur contenu dans une ``<iframe>``
    qu'on ne peut pas atteindre en navigation directe (le serveur refuse le
    top-level). On laisse donc l'iframe se charger et on repère la frame —
    principale ou imbriquée — où apparaît ``ready_selector``.
    """
    deadline = time.monotonic() + settings.nav_timeout_ms / 1000
    while time.monotonic() < deadline:
        for frame in page.frames:
            try:
                if frame.query_selector(ready_selector) is not None:
                    return frame
            except PlaywrightError:
                continue  # frame en cours de navigation/détachée
        if _looks_like_cloudflare(page):
            logger.info("Challenge Cloudflare en cours, attente…")
        page.wait_for_timeout(1_000)

    raise PlaywrightTimeoutError(
        f"Liste introuvable (sélecteur '{ready_selector}') dans la page ni ses iframes."
    )


def goto_and_wait(surface: Surface, url: str, settings: Settings, ready_selector: str) -> None:
    """Navigue ``surface`` (page ou iframe) vers ``url`` et attend la liste utile.

    - Tolère un challenge Cloudflare transitoire : patiente tant que la page
      ressemble à un écran d'attente, jusqu'à ce que ``ready_selector`` apparaisse.
    - Les erreurs réseau (ex. connexion refusée) sont réessayées un petit nombre de
      fois, puis remontées avec un message clair (inutile d'insister 45 s).

    Lève ``PlaywrightTimeoutError`` / ``PlaywrightError`` si la liste n'arrive jamais.
    """
    deadline = time.monotonic() + settings.nav_timeout_ms / 1000
    network_errors = 0
    last_error: PlaywrightError | None = None

    while time.monotonic() < deadline:
        try:
            surface.goto(url, wait_until="domcontentloaded", timeout=settings.nav_timeout_ms)
            network_errors = 0
        except PlaywrightError as exc:
            network_errors += 1
            last_error = exc
            if network_errors >= 3:
                raise PlaywrightError(f"Navigation vers {url} impossible : {exc}") from exc
            logger.debug("Navigation interrompue (%s), nouvelle tentative…", exc)
            surface.wait_for_timeout(1_000)
            continue

        try:
            surface.wait_for_selector(ready_selector, timeout=3_000, state="attached")
            return  # page utile prête
        except PlaywrightTimeoutError:
            if _looks_like_cloudflare(surface):
                logger.info("Challenge Cloudflare en cours, attente…")
                surface.wait_for_timeout(2_000)
            else:
                surface.wait_for_timeout(1_000)  # laisser le DOM se peupler

    # Dernière tentative explicite pour produire une erreur claire si ça échoue.
    surface.wait_for_selector(ready_selector, timeout=settings.nav_timeout_ms, state="attached")
