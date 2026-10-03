"""Configuration pilotée par variables d'environnement (préfixe ``SCRAPER_``).

Les valeurs peuvent venir de l'environnement ou d'un fichier ``.env`` à la racine.
Voir ``.env.example`` pour la liste complète.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Chemin absolu vers le .env à la racine du projet, indépendant du répertoire
# courant au moment du lancement (important pour torrent-dashboard).
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCRAPER_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Site cible — changeable sans toucher au code.
    base_url: str = "https://www.cpasbien2.cc/category/films"

    # Navigateur visible par défaut (aide à franchir Cloudflare et à déboguer).
    headless: bool = False

    # Chemin de la page « liste complète des films » (relatif au domaine courant).
    list_path: str = "/category/films"

    # Limites et critères de filtrage.
    max_films: int = 100
    max_size_gb: float = 5.0

    # Années recherchées dans le titre, sous forme brute « 2025,2026 ».
    years_raw: str = Field(default="2025,2026", alias="SCRAPER_YEARS")

    # Domaines tiers supplémentaires à autoriser.
    allowed_domains_raw: str = Field(default="", alias="SCRAPER_ALLOWED_DOMAINS")

    # Sortie.
    output_dir: str = "downloads"

    # Webservice Rico (vérification présence film).
    rico_api_url: str = "http://localhost:3000"

    # TMDB — fournir l'un ou l'autre (bearer_token prioritaire).
    tmdb_bearer_token: str = ""
    tmdb_api_key: str = ""

    # Transmission BitTorrent (envoi des torrents).
    transmission_url:  str = "http://ricohoho.fr:9091"
    transmission_user: str = ""
    transmission_pass: str = ""
    transmission_dir:  str = "/home/streaming/films"

    # Délais (millisecondes).
    nav_timeout_ms: int = 45_000
    request_delay_ms: int = 1_000

    # Authentification Web (JWT) & Sécurité.
    admin_user: str = "admin"
    admin_pass: str = "admin"
    jwt_secret: str = "torrent-scraper-secret-key-change-me"
    jwt_expires_hours: int = 24

    @property
    def years(self) -> list[str]:
        """Liste des années recherchées, normalisée."""
        return [y.strip() for y in self.years_raw.split(",") if y.strip()]

    @property
    def allowed_domains(self) -> list[str]:
        """Domaines supplémentaires autorisés (en plus de celui de base_url)."""
        return [d.strip() for d in self.allowed_domains_raw.split(",") if d.strip()]