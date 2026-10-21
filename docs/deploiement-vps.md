# Déploiement sur un VPS (Docker Compose, Nginx, HTTPS Let's Encrypt)

Ce guide installe SMS Gateway sur un serveur Linux neuf, accessible en HTTPS sur votre domaine, avec renouvellement automatique du certificat et sauvegardes quotidiennes.

```
Internet ──443──► nginx ──► web (gunicorn + gevent, 1 worker) ──► db (PostgreSQL 16)
             80 ──► nginx (challenge ACME + redirection HTTPS)
                    certbot (renouvellement toutes les 12 h)     backup (pg_dump quotidien)
```

## 1. Prérequis

| Élément | Recommandation |
|---|---|
| Serveur | VPS Ubuntu 24.04 LTS (ou Debian 12), 2 vCPU, 2 Go de RAM, 20 Go de disque : suffisant pour 1 à 5 téléphones et des dizaines de milliers de SMS par mois |
| Domaine | Un sous-domaine dédié, par exemple `sms.votre-entreprise.ma`, avec un enregistrement **A** vers l'IP du VPS (et **AAAA** si IPv6) |
| Ports | 22 (SSH), 80 et 443 ouverts vers le serveur |
| Accès | Compte avec `sudo`, connexion SSH par clé |

Vérifiez la résolution DNS avant de commencer. La commande doit renvoyer l'IP du VPS :

```bash
dig +short sms.votre-entreprise.ma
```

## 2. Préparer le serveur

### 2.1 Mises à jour et pare-feu

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install ufw fail2ban unattended-upgrades curl git
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo dpkg-reconfigure -plow unattended-upgrades
sudo timedatectl set-timezone Africa/Casablanca
```

> Docker ouvre lui-même les ports qu'il publie, sans passer par ufw. Dans cette configuration, seul Nginx publie des ports (80 et 443) : PostgreSQL et l'application ne sont jamais exposés.

### 2.2 Durcir SSH (recommandé)

Dans `/etc/ssh/sshd_config` : `PasswordAuthentication no` et `PermitRootLogin no`, puis :

```bash
sudo systemctl restart ssh
```

Vérifiez d'abord que votre clé SSH fonctionne depuis une seconde session.

### 2.3 Installer Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Déconnectez-vous puis reconnectez-vous, et vérifiez :

```bash
docker compose version
```

## 3. Installer l'application

### 3.1 Récupérer le code

Avec Git :

```bash
sudo mkdir -p /opt/smsgateway && sudo chown $USER: /opt/smsgateway
git clone <url-du-depot> /opt/smsgateway
cd /opt/smsgateway/deploy
```

Ou par copie depuis votre PC (dossier `sms/`) :

```bash
scp -r sms/ utilisateur@IP_DU_VPS:/opt/smsgateway
```

### 3.2 Générer la configuration

```bash
chmod +x *.sh
./setup.sh sms.votre-entreprise.ma admin@votre-entreprise.ma
```

Le script crée `deploy/.env` (droits 600) avec des secrets aléatoires : mot de passe PostgreSQL, `SECRET_KEY` et clé de chiffrement Fernet.

> **Conservez une copie de `deploy/.env` hors du serveur** (gestionnaire de mots de passe). Sans `APP_ENCRYPTION_KEYS`, les secrets des webhooks d'une sauvegarde restaurée ne sont plus déchiffrables.

### 3.3 Obtenir le certificat HTTPS et démarrer

```bash
./init-letsencrypt.sh
```

Le script :

1. vérifie que le domaine pointe bien vers ce serveur ;
2. construit l'image de l'application ;
3. démarre Nginx avec un certificat temporaire ;
4. obtient le certificat Let's Encrypt (challenge HTTP-01) ;
5. démarre tous les services.

Les migrations de base de données s'appliquent automatiquement au démarrage du conteneur `web`.

> Pour un premier essai, `./init-letsencrypt.sh --staging` utilise le serveur de test Let's Encrypt, sans limite de quota. Le navigateur affichera alors un avertissement. Relancez ensuite sans l'option.

### 3.4 Créer le premier administrateur

```bash
./manage.sh create-admin --email vous@votre-entreprise.ma --name "Votre nom"
```

Le mot de passe est demandé de façon interactive : 10 caractères minimum, avec majuscules, minuscules et chiffres.

Ouvrez `https://sms.votre-entreprise.ma` et connectez-vous.

## 4. Connecter les téléphones

1. Compilez l'APK signé (voir [android/README.md](../android/README.md)), puis copiez-le dans `deploy/downloads/sms-gateway.apk`.
   Il est servi sur `https://sms.votre-entreprise.ma/downloads/sms-gateway.apk`.
2. Sur le téléphone : téléchargez puis installez l'APK, accordez les permissions et désactivez l'optimisation de batterie.
3. Dans le tableau de bord : **Appareils › Ajouter un appareil › Générer le QR code**, puis scannez-le avec l'application.
   Le QR contient l'adresse HTTPS du serveur et un code à usage unique valable 10 minutes.
4. Réglez les limites d'envoi de chaque téléphone : par défaut 1 SMS toutes les 5 s, 100 par heure, 500 par jour.

Le téléphone se connecte en WebSocket (`wss://…/socket.io/`). Si la connexion est coupée, il bascule automatiquement en polling HTTPS et rejoue sa file locale au retour du réseau.

## 5. Exploitation courante

### 5.1 Commandes utiles

Toutes ces commandes se lancent depuis `/opt/smsgateway/deploy`.

| Action | Commande |
|---|---|
| État des services | `docker compose ps` |
| Logs en direct | `docker compose logs -f web` (ou `nginx`, `backup`, `certbot`) |
| Redémarrer l'application | `docker compose restart web` |
| Arrêter / démarrer | `docker compose down` / `docker compose up -d` |
| Créer une clé API | `./manage.sh create-api-key --name "ERP" --scopes messages:send,messages:read` |
| Exécuter un job tout de suite | `./manage.sh run-job dispatcher` |
| Version du schéma | `./manage.sh db current` |
| Santé | `curl -fsS https://sms.votre-entreprise.ma/healthz` |

### 5.2 Mettre à jour

```bash
cd /opt/smsgateway && git pull
cd deploy && ./update.sh
```

Le script fait une sauvegarde, reconstruit l'image, applique les migrations, redémarre, puis vérifie `/healthz`.

Pendant les quelques secondes de redémarrage, les téléphones gardent leurs SMS en file locale et se reconnectent seuls.

### 5.3 Sauvegardes et restauration

- Le conteneur `backup` exécute `pg_dump` chaque jour à **02:30** (heure de Casablanca) dans `deploy/backups/`, avec une rétention de **14 jours**. Ces valeurs se règlent dans `.env` (`BACKUP_TIME`, `BACKUP_RETENTION_DAYS`, `BACKUP_DIR`).
- Sauvegarde immédiate :

  ```bash
  docker compose exec backup /bin/sh /usr/local/bin/backup.sh now
  ```

- **Copie hors site** (indispensable) : synchronisez `deploy/backups/` vers un autre serveur ou un stockage objet, par exemple avec `rclone` ou `rsync` dans la crontab de l'hôte :

  ```cron
  15 3 * * * rsync -a --delete /opt/smsgateway/deploy/backups/ backup@autre-serveur:/sauvegardes/sms/
  ```

- Restauration (une sauvegarde de l'état courant est faite juste avant) :

  ```bash
  ./restore.sh backups/sms_20261021_023000.dump
  ```

- Testez une restauration au moins une fois par trimestre, sur un serveur de test.

### 5.4 Certificat HTTPS

Le conteneur `certbot` tente un renouvellement toutes les 12 h : Let's Encrypt ne renouvelle qu'à moins de 30 jours de l'échéance. Nginx recharge sa configuration toutes les 6 h. Vous n'avez rien à faire.

Vérification manuelle :

```bash
docker compose run --rm --entrypoint certbot certbot certificates
```

### 5.5 Rotation des secrets

- **Clé de chiffrement** : ajoutez une nouvelle clé **en tête** de `APP_ENCRYPTION_KEYS` (séparée par une virgule), lancez `docker compose up -d web` puis `./manage.sh rotate-keys`. Vous pourrez ensuite retirer l'ancienne clé.
- **Clés API** : bouton « Rotation » dans **API & webhooks**. L'ancienne clé est révoquée immédiatement.
- **Token d'un téléphone** : **Appareils › fiche › Réappairer** (nouveau QR, même historique).

## 6. Sécurité et conformité

- HTTPS obligatoire (redirection automatique, HSTS d'un an, TLS 1.2 et 1.3 uniquement), en-têtes CSP, `X-Frame-Options` et `nosniff`.
- Limitation de débit par IP dans Nginx (connexion : 10 par minute, API : 20 par seconde), en plus des limites par clé API et du seuil anti-abus de l'application.
- La base n'est jamais exposée : réseau Docker `internal`, sans accès Internet.
- Les cookies de session sont `Secure`, `HttpOnly` et `SameSite=Lax`. Les mots de passe sont hachés en argon2id, les tokens d'appareil et les clés API en SHA-256.
- L'application refuse de démarrer si `SECRET_KEY` ou `APP_ENCRYPTION_KEYS` sont absents ou laissés par défaut.
- **Loi 09-08** : le traitement de données personnelles doit être déclaré à la **CNDP**, et autorisé si nécessaire. La plateforme fournit le consentement par contact, le STOP automatique, l'export et l'effacement des données d'un contact, la purge des imports après 30 jours et un journal d'audit. La démarche administrative reste à votre charge.

## 7. Dépannage

| Symptôme | Cause probable et solution |
|---|---|
| `init-letsencrypt.sh` échoue au challenge | DNS pas encore propagé, ou port 80 bloqué (ufw, pare-feu de l'hébergeur). Vérifiez avec `curl -I http://votre-domaine/.well-known/acme-challenge/test` (une 404 venant de nginx est normale). |
| « too many certificates » | Quota Let's Encrypt atteint : attendez, et faites vos essais avec `--staging`. |
| Le téléphone reste « hors ligne » | Ouvrez l'app : le journal affiche l'erreur. Vérifiez l'heure du téléphone (TLS), l'optimisation de batterie, et le « démarrage automatique » sur Xiaomi, Huawei ou Oppo. |
| Téléphone connecté en « mode secours » seulement | Le WebSocket est bloqué : vérifiez que `/socket.io/` est bien relayé par Nginx avec l'en-tête `Upgrade` (proxy intermédiaire, CDN). Le polling HTTPS continue de fonctionner. |
| Les SMS restent « En attente » | Fenêtre horaire fermée (Paramètres), limite horaire ou journalière du téléphone atteinte, ou aucun téléphone en ligne. Voir la fiche de l'appareil. |
| `web` redémarre en boucle | `docker compose logs web` : secret manquant, ou base inaccessible (`POSTGRES_PASSWORD` modifié après la création du volume). |
| Erreur 413 à l'import | Fichier supérieur à 10 Mo : `MAX_UPLOAD_MB` dans `.env` et `client_max_body_size` dans `nginx/templates/default.conf.template`. |

## 8. Fichiers de déploiement

```
deploy/
├── docker-compose.yml             services db, web, nginx, certbot, backup
├── .env.example                   modèle (le vrai .env est généré par setup.sh)
├── setup.sh                       génération de .env avec des secrets aléatoires
├── init-letsencrypt.sh            premier certificat + démarrage
├── update.sh                      mise à jour sans perte (sauvegarde, migrations)
├── restore.sh                     restauration d'une sauvegarde
├── manage.sh                      commandes d'administration dans le conteneur
├── backup/backup.sh               pg_dump quotidien + rotation
├── nginx/templates/default.conf.template
├── nginx/snippets/                TLS, en-têtes de sécurité, en-têtes proxy
└── downloads/                     APK Android servi en HTTPS
```
