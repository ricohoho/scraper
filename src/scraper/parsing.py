"""Extraction des données depuis le DOM + parsing de la taille.

Les sélecteurs CSS sont regroupés ci-dessous. Ils ont été calés sur la structure
réelle de cpasbien (table.table-corps / a.titre / div.poid). Si le site change,
c'est le seul endroit à ajuster ; le reste du programme reste inchangé.

Les URLs (détail, page suivante, .torrent) sont lues via la **propriété ``href``
du DOM**, déjà résolue en absolu par le navigateur : pas de reconstruction
manuelle, donc robuste aux redirections et aux liens relatifs.
"""

from __future__ import annotations

import logging
import re

from playwright.sync_api import Page

from .models import Film

logger = logging.getLogger(__name__)

# --- Sélecteurs de la page LISTE -------------------------------------------------
# Une ligne de film (la liste est un tableau ; chaque <tr> contient un film).
ROW_SELECTOR = "table.table-corps tbody tr"
# Lien du titre dans une ligne. Son attribut `title` porte le titre complet,
# et sa propriété href donne l'URL de détail.
TITLE_LINK_SELECTOR = "a.titre"
# Élément contenant la taille, dans une ligne.
SIZE_SELECTOR = "div.poid"
# Sélecteur indiquant que la liste est chargée (utilisé pour attendre Cloudflare).
LIST_READY_SELECTOR = "table.table-corps a.titre"
# Lien « page suivante » de la pagination (plusieurs variantes tolérées).
NEXT_PAGE_SELECTOR = (
    "a[rel='next'], .pagination a.suivant, .navigation a.suivant, "
    "a.suivante, a:has-text('Suivant'), .pagination a:has-text('»')"
)

# Iframe par laquelle une « passerelle » (ex. ww1.lat/cpasbien/) embarque le vrai
# site. Si présente, on navigue directement vers son URL au lieu de la coquille.
CONTENT_IFRAME_SELECTOR = "iframe#main-iframe, iframe[src*='home'], iframe[src^='http']"

# --- Sélecteurs de la page DÉTAIL ------------------------------------------------
# Le lien de téléchargement enveloppe l'image telecharger.gif.
TORRENT_LINK_SELECTOR = "a:has(img[src*='telecharger'])"
# Présence de ce lien = page de détail prête.
DETAIL_READY_SELECTOR = TORRENT_LINK_SELECTOR

# --- Parsing de la taille --------------------------------------------------------
# Capture « 1.46 Go », « 950 Mo », « 2,3 GB », « 700 MB », « 1.2 To »…
_SIZE_RE = re.compile(r"([\d]+(?:[.,]\d+)?)\s*(To|Tb|TB|Go|Gb|GB|Mo|Mb|MB|Ko|Kb|KB)", re.IGNORECASE)
_UNIT_TO_GB = {
    "t": 1024.0,  # To/Tb
    "g": 1.0,     # Go/Gb
    "m": 1 / 1024.0,   # Mo/Mb
    "k": 1 / (1024.0 * 1024.0),  # Ko/Kb
}

# Récupère l'URL absolue résolue par le navigateur pour un <a>.
_HREF_PROP = "el => el.href"


def parse_size(text: str | None) -> float | None:
    """Convertit une taille textuelle (« 1.46 Go », « 950 Mo ») en gigaoctets."""
    if not text:
        return None
    match = _SIZE_RE.search(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    unit_key = match.group(2)[0].lower()
    factor = _UNIT_TO_GB.get(unit_key)
    return round(value * factor, 4) if factor is not None else None


def extract_films(page: Page) -> list[Film]:
    """Extrait les films visibles sur la page liste courante."""
    films: list[Film] = []
    for row in page.query_selector_all(ROW_SELECTOR):
        link = row.query_selector(TITLE_LINK_SELECTOR)
        if link is None:
            continue
        # Le titre complet est dans l'attribut `title` (le texte affiché est tronqué).
        title = (link.get_attribute("title") or link.inner_text() or "").strip()
        detail_url = link.evaluate(_HREF_PROP)
        if not title or not detail_url:
            continue

        size_el = row.query_selector(SIZE_SELECTOR)
        size_text = (size_el.inner_text() if size_el else "").strip() or None

        films.append(Film(
            title=title,
            size_gb=parse_size(size_text),
            detail_url=detail_url,
            size_raw=size_text,
        ))
    return films


def find_next_page_url(page: Page) -> str | None:
    """Renvoie l'URL de la page suivante, ou None s'il n'y en a pas."""
    link = page.query_selector(NEXT_PAGE_SELECTOR)
    return link.evaluate(_HREF_PROP) if link is not None else None


def extract_torrent_url(page: Page) -> str | None:
    """Sur une page de détail, renvoie l'URL absolue du fichier .torrent."""
    link = page.query_selector(TORRENT_LINK_SELECTOR)
    if link is None:
        logger.warning("Lien telecharger.gif introuvable sur %s", page.url)
        return None
    return link.evaluate(_HREF_PROP)
