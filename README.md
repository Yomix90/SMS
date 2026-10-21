# SMS Gateway

Plateforme de passerelle SMS : un téléphone Android, avec sa carte SIM, envoie et reçoit les SMS demandés par un tableau de bord web bilingue (FR/AR) et par une API REST.

```
Tableau de bord / API REST ──► Backend Flask (file d'attente, planification, webhooks)
                                   │  Socket.IO (wss) + repli HTTP
                                   ▼
                          Téléphone Android (service de premier plan, Room)
                                   │  SmsManager
                                   ▼
                                Opérateur
```

| Dossier | Contenu | Documentation |
|---|---|---|
| `backend/` | Flask 3, SQLAlchemy 2, Socket.IO, APScheduler, API REST + Swagger, tableau de bord Jinja/Bootstrap 5 (FR/AR, RTL, mode sombre) | [backend/README.md](backend/README.md) |
| `android/` | Application Kotlin (minSdk 26) : passerelle SMS, double SIM, mode hors ligne, appairage QR | [android/README.md](android/README.md) |
| `docs/` | Architecture et schéma de base de données · documentation API (exemples Python, PHP, JavaScript, cURL) | [architecture](docs/01-architecture-et-schema.md) · [API](docs/api.md) |
| `samples/` | Fichiers d'exemple pour l'import (Excel et CSV : variables, message par ligne, doublon, fixe, invalide) | [import_exemple.xlsx](samples/import_exemple.xlsx) |
| `deploy/` | Docker Compose (PostgreSQL, Nginx, Certbot, sauvegardes), scripts d'installation et de mise à jour | [docs/deploiement-vps.md](docs/deploiement-vps.md) |

## Démarrage rapide (local)

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements-dev.txt
python manage.py db upgrade
python manage.py create-admin --email admin@exemple.ma
python wsgi.py
```

Ouvrir http://localhost:5000, puis **Appareils › Ajouter un appareil**, et scanner le QR avec l'application Android.
Sur le réseau local, définissez `PUBLIC_BASE_URL=http://<IP-du-PC>:5000` dans `backend/.env` pour que le QR soit joignable depuis le téléphone.

## Tests

```bash
cd backend && pytest
cd android && ./gradlew assembleDebug lintDebug
```
