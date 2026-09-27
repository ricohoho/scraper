# Architecture du Code Python — Torrent Scraper

Ce document décrit l'organisation modulaire du projet **Torrent Scraper**, les responsabilités de chaque fichier Python et le graphe d'appels entre les différents modules.

---

## 📊 Graphe d'Appels et Dépendances (Mermaid)

```mermaid
flowchart TD
    %% Points d'entrée (CLI)
    subgraph CLI["🚀 Points d'entrée (CLI & App)"]
        WCLI["webapp_cli.py<br/>(torrent-dashboard)"]
        CLI["cli.py<br/>(torrent-scraper)"]
        ECLI["enrich_cli.py<br/>(torrent-enrich)"]
        SCLI["serve_cli.py<br/>(torrent-serve)"]
    end

    %% Couche Application & Web
    subgraph WEB["🌐 Couche Application & Dashboard"]
        WEBAPP["webapp.py<br/>(App Flask & Routes SSE)"]
        SERVER["server.py<br/>(Micro-serveur HTTP stdlib)"]
        HTML["html_report.py<br/>(Génération HTML Inline)"]
    end

    %% Couche Orchestration & Métier
    subgraph CORE["⚙️ Cœur Métier & Pipeline"]
        PIPE["pipeline.py<br/>(Orchestrateur Scraping)"]
        ENRICH["enrichment.py<br/>(Enrichissement TMDB & Rico)"]
        REPORT["report.py<br/>(Gestion JSON horodaté)"]
    end

    %% Couche Navigateur & Extraction
    subgraph SCRAPE["🌐 Navigateur & Parsing"]
        BROWSER["browser.py<br/>(Playwright & Cloudflare)"]
        PARSING["parsing.py<br/>(Extraction DOM & Sélecteurs)"]
        NAMING["naming.py<br/>(Nettoyage des fichiers)"]
    end

    %% Couche Modèles & Config
    subgraph BASE["📦 Configuration & Modèles"]
        CONFIG["config.py<br/>(Settings Pydantic)"]
        MODELS["models.py<br/>(Film, ScrapeResult)"]
    end

    %% Liaisons / Dépendances
    WCLI --> WEBAPP
    CLI --> PIPE
    ECLI --> ENRICH
    SCLI --> SERVER

    WEBAPP --> PIPE
    WEBAPP --> ENRICH
    WEBAPP --> HTML
    WEBAPP --> CONFIG

    SERVER --> HTML

    PIPE --> BROWSER
    PIPE --> PARSING
    PIPE --> NAMING
    PIPE --> REPORT
    PIPE --> ENRICH
    PIPE --> MODELS
    PIPE --> CONFIG

    ENRICH --> MODELS
    ENRICH --> CONFIG
    BROWSER --> CONFIG
```

---

## 📁 Détail du Rôle de Chaque Fichier

### 1. Points d'entrée (`*_cli.py` & `cli.py`)
- **`webapp_cli.py` (`torrent-dashboard`)** : Point d'entrée principal. Il démarre l'application Flask (`webapp.py`) sur le port `8765` et ouvre automatiquement le navigateur.
- **`cli.py` (`torrent-scraper`)** : Commande en ligne pour lancer un scraping direct en console sans interface web.
- **`enrich_cli.py` (`torrent-enrich`)** : Commande CLI pour enrichir un fichier JSON existant avec les métadonnées TMDB et la présence locale Rico.
- **`serve_cli.py` (`torrent-serve`)** : Commande CLI pour visualiser un rapport de scraping existant via un serveur HTTP simple (`server.py`).

### 2. Application Web & Rendu (`webapp.py`, `server.py`, `html_report.py`)
- **`webapp.py`** : Application Flask complète (11 routes HTTP, Server-Sent Events pour le progrès en temps réel, intégration RPC Transmission). Il lance le scraping dans un thread d'arrière-plan synchrone.
- **`server.py`** : Serveur HTTP léger basé sur la bibliothèque standard (`http.server`) utilisé par `torrent-serve`.
- **`html_report.py`** : Génère le code HTML/CSS/JS autonome pour afficher la vue interactive des films.

### 3. Orchestration & Traitement (`pipeline.py`, `enrichment.py`, `report.py`)
- **`pipeline.py`** : Orchestre le processus complet :
  1. Démarre Playwright via `browser.py`.
  2. Parcourt la liste des films et extrait les données via `parsing.py`.
  3. Télécharge les fichiers `.torrent` en utilisant `naming.py` pour sécuriser les noms.
  4. Génère le rapport JSON brut via `report.py`.
  5. Appelle `enrichment.py` pour ajouter les données TMDB et Rico.
- **`enrichment.py`** : Interroge l'API TMDB (Bearer Token) pour récupérer l'affiche, la note et la description, et vérifie auprès du serveur Rico si le film est déjà présent dans votre médiathèque locale.
- **`report.py`** : Fonctions de sauvegarde et chargement des fichiers JSON horodatés dans le dossier `downloads/`.

### 4. Navigateur & DOM (`browser.py`, `parsing.py`, `naming.py`)
- **`browser.py`** : Initialise la session Playwright Chromium synchrone, gère le contournement des protections Cloudflare (attente d'iframe Turnstile) et la navigation réseau.
- **`parsing.py`** : Contient tous les sélecteurs CSS du site cible. Extrait les liens, titres, tailles et catégories à partir du DOM HTML.
- **`naming.py`** : Nettoie les chaînes de caractères pour éviter les caractères invalides dans les noms de fichiers `.torrent`.

### 5. Configuration & Modèles (`config.py`, `models.py`)
- **`config.py`** : Définit les réglages de l'application via `pydantic-settings` (charge automatiquement le fichier `.env` ou les variables système avec préfixe `SCRAPER_`).
- **`models.py`** : Définition des structures de données Pydantic (`Film`, `DownloadOutcome`, `ScrapeResult`).
