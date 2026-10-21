# SMS Gateway : backend

Flask 3, SQLAlchemy 2, Socket.IO, APScheduler. SQLite en dev, PostgreSQL en prod.

## Démarrage en développement

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows (Linux/macOS : source .venv/bin/activate)
pip install -r requirements-dev.txt
cp .env.example .env            # puis renseigner PUBLIC_BASE_URL avec l'IP du PC sur le Wi-Fi
python manage.py db upgrade     # crée instance/sms.db
python manage.py create-admin --email admin@exemple.ma
python manage.py create-api-key --name "Mon ERP"
python manage.py seed-demo      # facultatif : contacts, modèles, automatisations de démo
python wsgi.py                  # http://localhost:5000 : Swagger sur /api/docs
```

Appairer un téléphone sans tableau de bord : `python manage.py pairing-code --name "A14" --svg qr.svg`.

## Commandes utiles

| Commande | Rôle |
|---|---|
| `python manage.py run-job dispatcher` | Exécuter un job immédiatement (`dispatcher`, `schedules`, `automations`, `webhooks`, `offline_devices`, `purge_imports`) |
| `python manage.py rotate-keys` | Re-chiffrer les secrets après ajout d'une clé en tête de `APP_ENCRYPTION_KEYS` |
| `python manage.py db migrate -m "…"` | Générer une migration après modification des modèles |
| `pytest` | Lancer les tests (113) |

## Organisation

```
app/
  models/        schéma (29 tables)
  services/      logique métier : phone, sms_encoding, templating, importer, imports, queue,
                 dispatcher, devices, inbound, campaigns, schedules, automations, webhooks, reports…
  api/           API REST v1 (flask-smorest, OpenAPI)
  device_api/    API HTTP du téléphone (appairage + repli polling)
  sockets/       Socket.IO /device (téléphones) et /dashboard (navigateur)
  tasks.py       jobs planifiés (verrou en base)
  cli.py         commandes d'administration
```

## Production

`gunicorn -k gevent -w 1 wsgi:app` (un seul worker : Socket.IO et le scheduler partagent le process).
L'image Docker applique les migrations au démarrage. Voir `deploy/` (étape suivante).
