#!/usr/bin/env bash
# Commandes d'administration dans le conteneur (sans démarrer un second planificateur) :
#   ./manage.sh create-admin --email vous@exemple.ma
#   ./manage.sh create-api-key --name "ERP" --scopes messages:send,messages:read
#   ./manage.sh rotate-keys | seed-demo | run-job dispatcher | db current
set -euo pipefail
cd "$(dirname "$0")"
exec docker compose exec -e SMS_NO_SCHEDULER=1 web python manage.py "$@"
