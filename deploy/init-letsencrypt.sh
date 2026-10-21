#!/usr/bin/env bash
# Premier démarrage : obtient le certificat Let's Encrypt puis lance toute la pile.
#   1. certificat auto-signé temporaire pour que Nginx puisse démarrer en HTTPS ;
#   2. démarrage de Nginx, demande du vrai certificat (challenge HTTP-01 sur /.well-known) ;
#   3. remplacement du certificat temporaire et rechargement de Nginx.
# Option --staging : serveur de test Let's Encrypt (pas de limite de quota, certificat non reconnu).
set -euo pipefail
cd "$(dirname "$0")"

[[ -f .env ]] || { echo "Lancez d'abord ./setup.sh" >&2; exit 1; }
set -a; source .env; set +a

STAGING_ARG=""
[[ "${1:-}" == "--staging" ]] && STAGING_ARG="--staging"
LIVE="/etc/letsencrypt/live/${DOMAIN}"
COMPOSE="docker compose"

echo "→ Vérification DNS : ${DOMAIN}"
SERVER_IP=$(curl -fsS -4 https://api.ipify.org || true)
DNS_IP=$(getent ahostsv4 "${DOMAIN}" | awk 'NR==1{print $1}' || true)
if [[ -n "$SERVER_IP" && "$SERVER_IP" != "$DNS_IP" ]]; then
  echo "⚠ ${DOMAIN} pointe vers '${DNS_IP:-aucune IP}' alors que ce serveur est ${SERVER_IP}."
  read -rp "Continuer quand même ? [o/N] " ok
  [[ "$ok" =~ ^[oOyY]$ ]] || exit 1
fi

echo "→ Construction de l'image applicative"
$COMPOSE build web

if $COMPOSE run --rm --entrypoint sh certbot -c "test -f ${LIVE}/fullchain.pem && ! test -f ${LIVE}/.dummy"; then
  echo "✓ Un certificat existe déjà pour ${DOMAIN} : démarrage direct."
else
  echo "→ Certificat temporaire auto-signé"
  $COMPOSE run --rm --entrypoint sh certbot -c "
    mkdir -p ${LIVE} &&
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
      -keyout ${LIVE}/privkey.pem -out ${LIVE}/fullchain.pem -subj '/CN=localhost' &&
    touch ${LIVE}/.dummy"

  echo "→ Démarrage de Nginx"
  $COMPOSE up -d nginx

  echo "→ Suppression du certificat temporaire"
  $COMPOSE run --rm --entrypoint sh certbot -c "
    rm -rf /etc/letsencrypt/live/${DOMAIN} /etc/letsencrypt/archive/${DOMAIN} /etc/letsencrypt/renewal/${DOMAIN}.conf"

  echo "→ Demande du certificat Let's Encrypt"
  $COMPOSE run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/certbot \
    ${STAGING_ARG} -d "${DOMAIN}" --email "${LETSENCRYPT_EMAIL}" \
    --rsa-key-size 4096 --agree-tos --no-eff-email --non-interactive

  $COMPOSE exec nginx nginx -s reload
fi

echo "→ Démarrage de tous les services"
$COMPOSE up -d

echo
echo "✓ SMS Gateway disponible sur https://${DOMAIN}"
echo "  Créez le premier administrateur : ./manage.sh create-admin --email vous@exemple.ma"
