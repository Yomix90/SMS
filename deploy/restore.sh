#!/usr/bin/env bash
# Restaure une sauvegarde : ./restore.sh backups/sms_20261021_023000.dump
# ATTENTION : remplace intégralement la base actuelle (une sauvegarde de sécurité est faite avant).
set -euo pipefail
cd "$(dirname "$0")"

FILE="${1:-}"
[[ -f "$FILE" ]] || { echo "usage : ./restore.sh backups/sms_AAAAMMJJ_HHMMSS.dump" >&2; exit 1; }
set -a; source .env; set +a

echo "La base « ${POSTGRES_DB:-sms} » va être REMPLACÉE par : $FILE"
read -rp "Tapez RESTAURER pour confirmer : " confirm
[[ "$confirm" == "RESTAURER" ]] || { echo "Annulé."; exit 1; }

echo "→ Sauvegarde de sécurité de l'état actuel"
docker compose exec -T backup /bin/sh /usr/local/bin/backup.sh now

echo "→ Arrêt de l'application (les téléphones gardent leurs envois en file locale)"
docker compose stop web

echo "→ Restauration"
docker compose exec -T db pg_restore --clean --if-exists --no-owner \
  -U "${POSTGRES_USER:-sms}" -d "${POSTGRES_DB:-sms}" < "$FILE"

echo "→ Redémarrage (migrations appliquées si nécessaire)"
docker compose start web
echo "✓ Restauration terminée."
