# Documentation de Déploiement — Torrent Scraper

Ce document détaille l'infrastructure Docker et le workflow de CI/CD (GitHub Actions) mis en place pour builder, publier et déployer l'application **Torrent Scraper** sur un serveur VPS.

---

## 🏗️ Architecture du Déploiement

Le processus de déploiement repose sur 3 composants principaux :

```
┌───────────────────────────┐     ┌───────────────────────────────────┐     ┌───────────────────────────┐
│     GitHub Repository     │ ──> │ GitHub Actions (CI/CD Pipeline)   │ ──> │    GitHub Package (GHCR)  │
│ (Push on main / master)   │     │ Environment: production           │     │ ghcr.io/owner/repo:latest │
└───────────────────────────┘     │ - Build Docker Image              │     └─────────────┬─────────────┘
                                  │ - Cache GHA & Push to GHCR        │                   │
                                  └───────────────────────────────────┘                   │
                                                                                          │ Docker Pull via SSH
                                                                                          ▼
                                                                            ┌───────────────────────────┐
                                                                            │       Serveur VPS         │
                                                                            │ Conteneur torrent-scraper │
                                                                            │ Port 8765 -> 8765         │
                                                                            └───────────────────────────┘
```

---

## 📁 Fichiers du Projet

### 1. `Dockerfile`
Fichier de construction de l'image Docker basée sur `python:3.11-slim` :
- Installe les dépendances système nécessaires à **Playwright** (Chromium).
- Installe les dépendances Python définies dans `pyproject.toml` et `requirements.txt`.
- Installe le navigateur Chromium Playwright (`python -m playwright install-deps chromium`).
- Expose le port HTTP `8765`.
- Point d'entrée par défaut : `torrent-dashboard --host 0.0.0.0 --port 8765`.

### 2. `.dockerignore`
Évite d'inclure les éléments inutiles ou sensibles dans le contexte du build Docker :
- `.venv`, `downloads/`, `.env`, `__pycache__`, `.git`, caches de tests.

### 3. `.github/workflows/deploy-scraper.yml`
Workflow GitHub Actions rattaché à l'environnement **`production`**, déclenché automatiquement lors d'un `push` sur `main` ou `master` (ou manuellement via `workflow_dispatch`) :
- **Job 1 : `build-and-push`** (environment: `production`)
  - S'authentifie sur **GitHub Container Registry (`ghcr.io`)** via `secrets.GITHUB_TOKEN`.
  - Utilise **Docker Buildx** et le système de cache GitHub Actions (`cache-from: type=gha`).
  - Génère et publie les tags `:latest` et `:<sha-git>`.
- **Job 2 : `deploy-vps`** (environment: `production`)
  - Se connecte en SSH au serveur VPS via `appleboy/ssh-action@v1.0.3`.
  - Effectue un `docker pull` de la nouvelle image depuis `ghcr.io`.
  - Arrête et supprime l'ancien conteneur `torrent-scraper` si présent.
  - Crée le dossier hôte `/opt/torrent-scraper/downloads` pour la persistance des fichiers `.torrent`.
  - Lance le nouveau conteneur avec redirection de port `-p 8765:8765` et relancement automatique `--restart unless-stopped`.

---

## 🔑 Configuration de l'Environnement `production` dans GitHub

### 1. Créer l'Environnement GitHub
1. Rendez-vous sur votre dépôt GitHub : **Settings > Environments**.
2. Cliquez sur **New environment**.
3. Saisissez exactement le nom : **`production`**.

### 2. Configurer les Secrets et Variables dans l'Environnement `production`

Une fois l'environnement **`production`** créé, ajoutez-y les secrets et variables :

#### 🔐 Environment Secrets (Informations sensibles)

| Nom du Secret | Description | Exemple |
| :--- | :--- | :--- |
| `VPS_KEY` | Clé privée SSH (format OpenSSH) autorisée sur le serveur VPS (`~/.ssh/authorized_keys`). | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `CR_PAT` *(ou `GHCR_TOKEN`)* | Jeton d'accès personnel GitHub (PAT) avec la portée `read:packages` (nécessaire si le dépôt/package GHCR est privé). | `ghp_xxxxxxxxxxxxxxxxxxxx` |
| `SCRAPER_TMDB_BEARER_TOKEN` | Jeton d'authentification Bearer TMDB (API v4) pour l'enrichissement des données de films. | `eyJhbGciOiJIUzI1NiJ9...` |
| `SCRAPER_TRANSMISSION_USER` | Nom d'utilisateur pour la connexion RPC Transmission. | `admin` |
| `SCRAPER_TRANSMISSION_PASS` | Mot de passe pour la connexion RPC Transmission. | `secretpassword` |

#### 🌐 Environment Variables (Configuration de l'environnement)

| Nom de la Variable | Description | Exemple / Valeur par défaut |
| :--- | :--- | :--- |
| `VPS_HOST` | Adresse IP ou Nom de domaine du serveur VPS. | `192.0.2.1` ou `vps.mondomaine.com` |
| `VPS_USER` | Nom de l'utilisateur SSH sur le VPS. | `root` ou `ubuntu` |
| `VPS_PORT` | Port du service SSH sur le VPS. | `22` |
| `SCRAPER_TRANSMISSION_URL` | URL de l'instance Transmission RPC. | `http://vps.mondomaine.com:9091` |
| `SCRAPER_TRANSMISSION_DIR` | Repertoire de destination des téléchargements sur le serveur Transmission. | `/home/streaming/films` |

---

## 🖥️ Prérequis et Maintenance sur le VPS

### 1. Prérequis sur le serveur VPS
- **Docker** doit être installé et le daemon en cours d'exécution :
  ```bash
  sudo systemctl status docker
  ```
- L'utilisateur SSH doit appartenir au groupe `docker` pour exécuter des commandes Docker sans `sudo` :
  ```bash
  sudo usermod -aG docker $USER
  ```
- Assurez-vous que le port `8765` est autorisé dans le pare-feu du VPS :
  ```bash
  sudo ufw allow 8765/tcp
  ```

### 2. Commandes utiles sur le VPS

```bash
# Vérifier l'état du conteneur
docker ps -f name=torrent-scraper

# Consulter les logs en temps réel
docker logs -f torrent-scraper

# Redémarrer manuellement le conteneur
docker restart torrent-scraper

# Accéder au shell à l'intérieur du conteneur
docker exec -it torrent-scraper bash
```
