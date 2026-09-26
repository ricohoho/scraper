"""Structures de données du domaine (indépendantes de Playwright et de la CLI)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Film:
    """Un film tel qu'extrait de la liste."""

    title: str
    size_gb: float | None  # None si la taille n'a pas pu être lue
    detail_url: str
    size_raw: str | None = None  # texte brut extrait du DOM (ex. « 2.57 Go »)


@dataclass
class DownloadOutcome:
    """Résultat du traitement d'un film retenu."""

    film: Film
    torrent_url: str | None = None
    saved_path: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.saved_path is not None and self.error is None


@dataclass
class ScrapeResult:
    """Résumé d'une exécution complète, renvoyé par le cœur (réutilisable en backend)."""

    films: list[Film] = field(default_factory=list)
    selected: list[Film] = field(default_factory=list)
    downloads: list[DownloadOutcome] = field(default_factory=list)

    @property
    def downloaded_count(self) -> int:
        return sum(1 for d in self.downloads if d.ok)
