# 🔐 Documentation : Implémentation de l'Authentification JWT & Sécurisation de l'API

## 1. Présentation générale

Cette documentation décrit le système de sécurité et d'authentification mis en place sur l'application **Torrent Scraper Dashboard**.
L'accès à l'interface web et à l'ensemble des API REST backend est protégé par des jetons **JWT (JSON Web Token)** signés en HMAC-SHA256, accompagnés d'une interface de connexion moderne (*Glassmorphism*).

---

## 2. Architecture de Sécurité

```
+------------------------+             +--------------------------+
|                        |  1. Login   |                          |
|   Navigateur Client    | ----------> |  API POST /api/login     |
|   (Dashboard HTML/JS)  | <---------- |  (Vérifie Admin/Pass)    |
|                        |   2. Token  |                          |
+------------------------+             +--------------------------+
            |
            | 3. Requêtes API sécurisées avec Header
            |    "Authorization: Bearer <token>"
            v
+-----------------------------------------------------------------+
|   Flask Backend (@app.before_request Middleware)                |
|   - Vérifie la signature HMAC-SHA256 & l'expiration du JWT      |
|   - Si Valide : Traite la requête API                           |
|   - Si Invalide : Renvoie HTTP 401 Unauthorized                 |
+-----------------------------------------------------------------+
```

---

## 3. Configuration & Variables d'environnement

Les identifiants et la clé secrète de signature sont définis dans `src/scraper/config.py` et modifiables via le fichier `.env` ou les variables d'environnement Docker / GitHub Secrets :

| Variable d'environnement | Valeur par défaut | Description |
| :--- | :--- | :--- |
| `SCRAPER_ADMIN_USER` | `admin` | Nom d'utilisateur administrateur |
| `SCRAPER_ADMIN_PASS` | `admin` | Mot de passe d'accès au Dashboard |
| `SCRAPER_JWT_SECRET` | `torrent-scraper-secret-key-change-me` | Clé secrète utilisée pour signer le jeton JWT |
| `SCRAPER_JWT_EXPIRES_HOURS` | `24` | Durée de validité du jeton en heures (24h) |

---

## 4. Endpoints API d'Authentification

### `POST /api/login` (Public)
Authentifie l'utilisateur et génère un jeton JWT.
* **Request Body** :
  ```json
  {
    "username": "admin",
    "password": "..."
  }
  ```
* **Response 200 OK** :
  ```json
  {
    "ok": true,
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "username": "admin",
    "expires_in": 86400
  }
  ```
* **Response 401 Unauthorized** :
  ```json
  {
    "ok": false,
    "error": "Identifiant ou mot de passe incorrect"
  }
  ```

### `GET /api/me` (Protégé)
Vérifie la validité du jeton JWT actuel.
* **Headers** : `Authorization: Bearer <token>`
* **Response 200 OK** : `{"authenticated": true, "username": "admin"}`
* **Response 401 Unauthorized** : `{"authenticated": false}`

---

## 5. Endpoints Protégés

Toutes les routes d'action et d'administration requièrent un jeton JWT valide :
* `GET /api/config`
* `POST /api/scrape/start`
* `GET /api/scrape/progress` (via paramètre query `?token=...` pour EventSource SSE)
* `GET /api/reports`
* `POST /api/report/activate`
* `GET /api/report-json/<name>`
* `GET /films/<name>`
* `POST /select`
* `GET /api/transmission/test`
* `POST /api/transmission/send`

En cas d'absence ou d'invalidité du jeton, le serveur répond :
```json
{
  "error": "Authentification requise",
  "code": "UNAUTHORIZED"
}
```

---

## 6. Fonctionnement Frontend (JavaScript)

1. **Stockage du Jeton** : Le jeton est conservé dans le `localStorage` sous la clé `torrent_scraper_token`.
2. **Wrapper `apiFetch(url, options)`** :
   Chaque appel réseau vers le backend injecte automatiquement le header `Authorization: Bearer <token>`.
   Si le serveur renvoie un code `401 Unauthorized`, `apiFetch` intercepte l'erreur et affiche le modal de connexion.
3. **Modal Login Glassmorphism** : Un overlay au design sombre avec effet de flou arrière-plan (`backdrop-filter: blur(16px)`) réclame les identifiants de connexion.
4. **Déconnexion** : Le bouton Déconnexion réinitialise le `localStorage` et affiche à nouveau l'interface de connexion.