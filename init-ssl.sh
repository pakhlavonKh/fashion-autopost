#!/usr/bin/env bash
set -e

# ==============================================================================
# Fashion Autopost - Let's Encrypt SSL Initialization Script
# ==============================================================================

if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Usage: ./init-ssl.sh <domain_name> <email_address>"
    echo "Example: ./init-ssl.sh fashion.example.com admin@example.com"
    exit 1
fi

DOMAIN="$1"
EMAIL="$2"

echo ">>> [1/5] Preparing environment for domain: $DOMAIN ($EMAIL)"

mkdir -p certbot/conf certbot/www nginx/conf.d

# Ensure initial HTTP server is up
echo ">>> [2/5] Starting HTTP reverse proxy..."
docker compose up -d nginx app

echo ">>> [3/5] Requesting Let's Encrypt SSL Certificate..."
docker compose run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot \
    --email \"$EMAIL\" \
    -d \"$DOMAIN\" \
    --rsa-key-size 4096 \
    --agree-tos \
    --no-eff-email \
    --force-renewal" certbot

echo ">>> [4/5] Applying SSL Nginx configuration..."
sed "s/\${DOMAIN}/$DOMAIN/g" nginx/conf.d/ssl.conf.template > nginx/conf.d/default.conf

echo ">>> [5/5] Reloading Nginx with HTTPS..."
docker compose exec nginx nginx -s reload

echo ""
echo "======================================================================"
echo " SUCCESS! SSL certificate installed and active for: https://$DOMAIN"
echo " Your Fashion Autopost Dashboard is live and secure!"
echo "======================================================================"
