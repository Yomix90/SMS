#!/usr/bin/env bash
# Mise à jour : sauvegarde, reconstruction de l'image, migrations (au démarrage du conteneur), redémarrage.
#   git pull (ou copie des nouveaux fichiers) puis ./update.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "→ Sauvegarde avant mise à jour"
docker compose exec -T backup /bin/sh /usr/local/bin/backup.sh now

echo "→ Reconstruction de l'image"
docker compose build web

echo "→ Redémarrage de l'application (migrations Alembic appliquées automatiquement)"
docker compose up -d --no-deps web
docker compose up -d

echo "→ Vérification"
for i in $(seq 1 30); do
  if docker compose exec -T web curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "✓ Application opérationnelle."
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 2
done
echo "✗ L'application ne répond pas : docker compose logs --tail=100 web" >&2
exit 1
