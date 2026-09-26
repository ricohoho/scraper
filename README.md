# torrent-scraper

Scraper de liste de films basé sur **Playwright** (vrai navigateur Chromium), capable
de franchir la plupart des challenges **Cloudflare**. Il analyse les *N* premiers films
d'un site, enrichit les métadonnées via **TMDB** et **Rico**, et dispose d'un
**dashboard web complet** pour tout piloter depuis le navigateur — jusqu'à l'envoi
vers **Transmission**.

> ⚠️ **Usage responsable** : assurez-vous d'avoir le droit d'accéder et de télécharger
> les contenus visés, et respectez le droit d'auteur ainsi que les CGU du site.

---

## Démarrage rapide

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium   # une seule fois
cp .env.example .env                    # puis renseigner les tokens TMDB / Transmission
torrent-dashboard                       # ouvre http://localhost:8765/
```

---

## Architecture

```
src/scraper/
  config.py       # Settings (variables d'env, préfixe SCRAPER_)
  models.py       # Film, DownloadOutcome, ScrapeResult
  browser.py      # session Playwright + gestion Cloudflare/iframe
  parsing.py      # extraction DOM  ← SÉLECTEURS À AJUSTER ICI
  naming.py       # noms de fichiers sûrs
  pipeline.py     # orchestration scraping (run(settings, progress=None))
  report.py       # génération du rapport JSON horodaté
  enrichment.py   # enrichissement : titre/année, Rico, TMDB
  html_report.py  # génération HTML statique et interactive
  server.py       # micro-serveur stdlib (torrent-serve)
  webapp.py       # dashboard Flask — 11 routes, SSE, Transmission RPC
  cli.py          # → torrent-scraper
  enrich_cli.py   # → torrent-enrich
  serve_cli.py    # → torrent-serve
  webapp_cli.py   # → torrent-dashboard
```

---

## Configuration

Copier `.env.example` en `.env` et renseigner :

**Scraper**

| Variable | Défaut | Rôle |
|---|---|---|
| `SCRAPER_BASE_URL` | `https://www.cpasbien2.cc/category/films` | Site cible |
| `SCRAPER_HEADLESS` | `false` | Navigateur sans interface (`true`) ou visible (`false`) |
| `SCRAPER_MAX_FILMS` | `100` | Nombre de films analysés |
| `SCRAPER_YEARS` | `2025,2026` | Années recherchées dans le titre |
| `SCRAPER_MAX_SIZE_GB` | `5` | Seuil de taille max (strictement inférieur) |
| `SCRAPER_OUTPUT_DIR` | `downloads` | Dossier de sortie (torrents + rapports) |

**Enrichissement**

| Variable | Défaut | Rôle |
|---|---|---|
| `SCRAPER_RICO_API_URL` | `http://localhost:3000` | Webservice de présence locale |
| `SCRAPER_TMDB_BEARER_TOKEN` | *(vide)* | Bearer token TMDB (prioritaire) |
| `SCRAPER_TMDB_API_KEY` | *(vide)* | Clé API TMDB (si pas de bearer token) |

Obtenir un token TMDB : <https://developer.themoviedb.org/reference/intro/authentication>

**Transmission**

| Variable | Défaut | Rôle |
|---|---|---|
| `SCRAPER_TRANSMISSION_URL` | `http://ricohoho.fr:9091` | URL du serveur Transmission |
| `SCRAPER_TRANSMISSION_USER` | *(vide)* | Login Transmission |
| `SCRAPER_TRANSMISSION_PASS` | *(vide)* | Mot de passe Transmission |
| `SCRAPER_TRANSMISSION_DIR` | `/home/streaming/films` | Dossier de destination sur le serveur |

---

## Dashboard web — `torrent-dashboard`

**Point d'entrée principal.** Lance une interface web complète sur `http://localhost:8765/`.

```bash
torrent-dashboard              # port 8765
torrent-dashboard --port 9000  # port alternatif
```

### Onglet ⚙️ Scraping

Formulaire pré-rempli depuis `.env`. Cliquer **▶ Lancer le scraping** :
- Le scraping Playwright s'exécute en arrière-plan
- La progression s'affiche en temps réel (SSE) : navigateur lancé, films trouvés, torrents téléchargés
- L'enrichissement Rico + TMDB démarre automatiquement à la fin
- Le rapport JSON est disponible immédiatement dans l'onglet Films

### Onglet 🎥 Films

Sélectionner un rapport JSON → les films s'affichent en cartes visuelles :
- **Bordure verte** : présent sur Rico (titre, année, taille)
- **Bordure jaune** : absent — affiche TMDB, résumé, acteurs, réalisateur, bouton bande-annonce

Cocher un film → `"selectionne": true` sauvegardé immédiatement dans le JSON.

### Onglet 📡 Transmission

- Renseigner l'URL, le login, le mot de passe et le dossier de destination
- Cliquer **🔌 Tester la connexion** pour vérifier l'accès
- Les films cochés dans l'onglet Films apparaissent automatiquement
- Cliquer **📡 Envoyer vers Transmission** — chaque torrent est envoyé via l'API RPC

---

## Commandes CLI (usage avancé)

### `torrent-scraper` — scraping seul

```bash
torrent-scraper --max 10 --headful -v          # 10 films, navigateur visible, logs détaillés
torrent-scraper --url https://exemple.tld/     # autre site
```

Génère `downloads/scraping-cpasbien-YYYY-MM-DD_HH-MM-SS.json`.

### `torrent-enrich` — enrichissement seul

```bash
torrent-enrich downloads/scraping-cpasbien-2026-06-13_18-51-21.json
torrent-enrich --rico-url http://mon-serveur:3000 fichier.json
```

Enrichit le JSON **en place** et génère `<fichier>.html`.

### `torrent-serve` — visualisation interactive seule

```bash
torrent-serve downloads/scraping-cpasbien-2026-06-13_18-51-21.json
# → http://localhost:8765/ (sans les onglets Scraping et Transmission)
```

---

## Résultats dans `downloads/`

```
scraping-cpasbien-2026-06-13_18-51-21.json   ← rapport enrichi (sélections incluses)
scraping-cpasbien-2026-06-13_18-51-21.html   ← page HTML statique
Blue Ruin.torrent
Le Cousin.torrent
…
```

---

## Ajuster les sélecteurs

Si le site change de structure HTML, modifier les constantes en haut de
[src/scraper/parsing.py](src/scraper/parsing.py) :
`ROW_SELECTOR`, `TITLE_LINK_SELECTOR`, `SIZE_SELECTOR`, `NEXT_PAGE_SELECTOR`,
`TORRENT_LINK_SELECTOR`. Le reste du code reste inchangé.

---

## Tests

```bash
python -m pytest
python tests/test_units.py   # sans dépendance externe
```
