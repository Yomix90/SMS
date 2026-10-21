#!/usr/bin/env bash
# Prépare deploy/.env avec des secrets aléatoires. Usage : ./setup.sh sms.exemple.ma admin@exemple.ma
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f .env ]]; then
  echo "deploy/.env existe déjà : rien n'est modifié (supprimez-le pour régénérer)."
  exit 0
fi

DOMAIN="${1:-}"
EMAIL="${2:-}"
[[ -z "$DOMAIN" ]] && read -rp "Nom de domaine (ex. sms.exemple.ma) : " DOMAIN
[[ -z "$EMAIL" ]] && read -rp "Email pour Let's Encrypt (alertes d'expiration) : " EMAIL
if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
  echo "Nom de domaine invalide : $DOMAIN" >&2
  exit 1
fi

rand_hex() { openssl rand -hex "$1"; }
# Clé Fernet : 32 octets en base64 « URL-safe »
fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }

sed \
  -e "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" \
  -e "s|^LETSENCRYPT_EMAIL=.*|LETSENCRYPT_EMAIL=${EMAIL}|" \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand_hex 24)|" \
  -e "s|^SECRET_KEY=.*|SECRET_KEY=$(rand_hex 32)|" \
  -e "s|^APP_ENCRYPTION_KEYS=.*|APP_ENCRYPTION_KEYS=$(fernet_key)|" \
  .env.example > .env
chmod 600 .env
mkdir -p backups downloads

echo "✓ deploy/.env créé pour ${DOMAIN} (permissions 600)."
echo "  Conservez une copie de APP_ENCRYPTION_KEYS et POSTGRES_PASSWORD hors du serveur :"
echo "  sans elles, une sauvegarde ne peut pas être restaurée entièrement."
echo "Étape suivante : ./init-letsencrypt.sh"
