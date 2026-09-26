# CLAUDE.md — Guide pour Claude Code

## Ce que fait ce projet

Scraper de films basé sur Playwright (Chromium) qui :
1. Liste les films d'un site torrent (cpasbien), télécharge les `.torrent`
2. Enrichit un rapport JSON via Rico (présence locale) et TMDB (metadata)
3. Expose un **dashboard web Flask** (`torrent-dashboard`) pour tout piloter depuis le navigateur
4. Envoie les torrents sélectionnés à un serveur **Transmission** via son API RPC

## Commandes clés

```bash
torrent-dashboard             # ← point d'entrée principal (port 8765)
torrent-scraper --max 10 -v   # scraping seul en CLI
torrent-enrich fichier.json   # enrichissement seul en CLI
torrent-serve fichier.json    # visualisation interactive seule
```

## Architecture des modules

```
src/scraper/
  config.py       # Settings pydantic-settings (préfixe SCRAPER_)
  models.py       # Film, DownloadOutcome, ScrapeResult
  browser.py      # session Playwright + gestion Cloudflare/iframe
  parsing.py      # extraction DOM ← SÉLECTEURS ICI si le site change
  naming.py       # sanitize_filename(), unique_torrent_path()
  pipeline.py     # run(settings, progress=None) → ScrapeResult
  report.py       # save_report() → JSON horodaté dans output_dir
  enrichment.py   # enrich_report() : parse_film_info, Rico, TMDB
  html_report.py  # _build_page(), serve_html(), save_html_report()
  server.py       # HTTPServer stdlib : _update_json() (toggle sélection)
  webapp.py       # App Flask : 11 routes, HTML dashboard inline
  cli.py          # torrent-scraper
  enrich_cli.py   # torrent-enrich
  serve_cli.py    # torrent-serve
  webapp_cli.py   # torrent-dashboard
```

## Dépendances

- `playwright` — navigateur Chromium (sync API uniquement)
- `pydantic-settings` — config via .env
- `flask` — dashboard web + SSE
- stdlib uniquement pour HTTP client (urllib), Transmission RPC, HTML report

## Conventions importantes

- **Pas de framework async** : Playwright utilise l'API synchrone. Le dashboard Flask lance le scraper dans un `threading.Thread` et communique via `queue.Queue` + SSE (`text/event-stream`).
- **JSON en place** : `enrich_report()` et `_update_json()` modifient le fichier JSON sur disque avec `json.dumps(..., ensure_ascii=False, indent=2)` — toujours ce format exact.
- **HTML inline** : CSS et JS sont des constantes string dans `html_report.py` et `webapp.py`. Pas de fichiers statiques séparés.
- **Un seul job à la fois** : `_job` dans `webapp.py` est global, protégé par `_job_lock`. Refuser un second lancement si `status == "running"`.
- **Rapport actif** : `_active_report` (Path) dans `webapp.py` désigne le JSON courant utilisé par `/select` et l'onglet Transmission.

## Flux du dashboard (onglet Scraping)

```
POST /api/scrape/start
  → thread démarre
  → pipeline.run(settings, progress=q.put)   # events: browser_launched, films_found, torrent_ok/error, scrape_done
  → enrich_report(json_path, settings)        # event: enriching, all_done
GET /api/scrape/progress (SSE EventSource)
  → lit q.get() → yield "data: {...}\n\n"
```

## Transmission RPC

1. `GET /transmission/rpc` → 409 avec header `X-Transmission-Session-Id`
2. `POST /transmission/rpc` avec `{"method":"torrent-add","arguments":{"metainfo":"<base64>","download-dir":"..."}}`
3. Header `Authorization: Basic <b64(user:pass)>` si credentials

## Variables d'env à configurer

```bash
# .env
SCRAPER_BASE_URL=https://www.cpasbien2.cc/category/films
SCRAPER_TMDB_BEARER_TOKEN=eyJ...
SCRAPER_RICO_API_URL=http://localhost:3000
SCRAPER_TRANSMISSION_URL=http://ricohoho.fr:9091
SCRAPER_TRANSMISSION_USER=
SCRAPER_TRANSMISSION_PASS=
SCRAPER_TRANSMISSION_DIR=/home/streaming/films
```

## Sélecteurs à ajuster si le site change

Fichier `parsing.py`, constantes en haut :
- `ROW_SELECTOR` — ligne de film dans le tableau
- `TITLE_LINK_SELECTOR` — lien du titre
- `SIZE_SELECTOR` — élément contenant la taille
- `NEXT_PAGE_SELECTOR` — lien pagination
- `TORRENT_LINK_SELECTOR` — lien de téléchargement (page détail)
