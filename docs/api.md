# API REST — SMS Gateway v1

Envoyez des SMS depuis vos applications (ERP, site e-commerce, CRM), suivez leur livraison et recevez les réponses de vos clients.

- **URL de base** : `https://sms.votre-entreprise.ma/api/v1`
- **Documentation interactive** (Swagger / OpenAPI 3) : `https://sms.votre-entreprise.ma/api/docs`. Spécification brute : `/api/openapi.json`.
- **Format** : JSON en UTF-8. L'arabe et les emojis sont acceptés.
- **Dates** : ISO 8601. Une date **sans fuseau** est interprétée en heure de **Casablanca** (`2026-11-05T09:30`). Les réponses sont en UTC (`2026-11-05T08:30:00+00:00`).

Des exemples complets et testés sont fournis plus bas en [Python](#python), [PHP](#php) et [JavaScript](#javascript-nodejs). La référence donne les exemples en cURL.

---

## 1. Authentification

Créez une clé dans **Tableau de bord › API & webhooks › Nouvelle clé**, ou avec `./manage.sh create-api-key`. La clé (`sk_…`) n'est affichée **qu'une seule fois**.

```http
Authorization: Bearer sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Chaque clé possède des **permissions** :

| Permission | Autorise |
|---|---|
| `messages:send` | envoyer, annuler, envois groupés, import de fichier |
| `messages:read` | lire les messages et leur statut |
| `inbox:read` | lire les SMS reçus |
| `contacts:read` / `contacts:write` | contacts et groupes |
| `templates:read` / `templates:write` | modèles de messages |
| `schedules:read` / `schedules:write` | envois planifiés |
| `campaigns:read` / `campaigns:write` | suivre et piloter les campagnes (pause, reprise, annulation) |
| `devices:read` | état des téléphones |
| `webhooks:manage` | créer et supprimer des webhooks |
| `*` | toutes les permissions |

Une clé peut aussi avoir une **limite de requêtes par minute**, un **quota de SMS par jour**, une **date d'expiration**, et l'autorisation d'envoyer **hors de la fenêtre horaire** (SMS transactionnels, OTP). Elle peut être révoquée ou renouvelée (rotation) à tout moment.

## 2. Limites de débit

Chaque réponse inclut :

```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 57
X-RateLimit-Reset: 42
```

`X-RateLimit-Reset` est le nombre de secondes avant la remise à zéro. Au-delà de la limite, l'API répond **429** avec `Retry-After`.

Trois autres garde-fous répondent aussi 429 :

- `quota_exceeded` : quota journalier de la clé atteint ;
- `abuse_suspected` : volume anormal en peu de temps. Les administrateurs sont alertés ;
- la limitation par IP de Nginx (20 requêtes par seconde).

## 3. Erreurs

Toutes les erreurs ont la même forme :

```json
{
  "error": {
    "code": "invalid_phone",
    "message": "Numéro invalide : 0522123456",
    "details": {"phone": "0522123456", "reason": "landline"}
  }
}
```

| HTTP | `code` | Signification |
|---|---|---|
| 401 | `missing_api_key`, `invalid_api_key` | clé absente, invalide, expirée ou révoquée |
| 403 | `insufficient_scope` | permission manquante (`details.required`) |
| 403 | `window_override_forbidden` | `ignore_window` demandé sans l'autorisation de la clé |
| 404 | `not_found` | ressource introuvable |
| 409 | `invalid_state`, `not_cancelable`, `contact_exists`, `group_exists`, `template_exists`, `device_disabled` | conflit avec l'état actuel |
| 413 | `payload_too_large` | fichier supérieur à 10 Mo |
| 422 | `validation_error` | champ manquant ou invalide (`details` par champ) |
| 422 | `invalid_phone` | numéro invalide ; `reason` vaut `invalid_format`, `landline` ou `invalid_number` |
| 422 | `message_too_long`, `empty_body` | plus de 10 segments, ou texte vide |
| 422 | `no_recipients`, `no_valid_recipients`, `phone_column_not_found`, `unreadable_file`, `unsupported_format` | envoi groupé ou import |
| 429 | `rate_limited`, `quota_exceeded`, `abuse_suspected` | voir §2 |

## 4. Numéros de téléphone

Les formats marocains sont normalisés en E.164 :

- `0612345678`, `06 12 34 56 78`, `06.12.34.56.78` ;
- `+212612345678`, `00212612345678`, `212612345678` ;
- `612345678` (zéro supprimé par Excel).

Le résultat est toujours `+212612345678`. Les **fixes** (`05…`, `08…`) sont refusés, car ils ne reçoivent pas de SMS. Les numéros étrangers sont acceptés au format international (`+33…`).

## 5. Cycle de vie d'un message

```
scheduled ─► queued ─► dispatched ─► sent ─► delivered
                 ▲          │          └───► failed
                 └──────────┘ (échec temporaire : nouvelle tentative automatique)
blocked  : numéro en liste noire / désinscrit (STOP)      canceled : annulé
```

| Statut | Sens |
|---|---|
| `scheduled` | programmé pour plus tard (`scheduled_at`) |
| `queued` | en file : en attente d'un téléphone disponible, de la fenêtre horaire ou d'une limite d'envoi |
| `paused` | campagne en pause |
| `dispatched` | transmis à un téléphone |
| `sent` | accepté par l'opérateur (accusé d'envoi du téléphone) |
| `delivered` | accusé de livraison reçu du destinataire |
| `failed` | échec définitif (`error_code`), après les nouvelles tentatives pour les erreurs temporaires |
| `blocked` | non envoyé : liste noire ou désinscription |
| `canceled` | annulé avant envoi |
| `received` | SMS entrant (boîte de réception) |

Codes d'erreur des téléphones :

- **temporaires**, relancés automatiquement : `GENERIC_FAILURE`, `RADIO_OFF`, `NO_SERVICE`, `NETWORK_ERROR`, `LIMIT_EXCEEDED`, `TIMEOUT`, `ACK_TIMEOUT` ;
- **définitifs** : `INVALID_NUMBER`, `NULL_PDU`, `DELIVERY_FAILED`, `NO_SIM`, `PERMISSION_DENIED`, `SHORT_CODE_NOT_ALLOWED`.

**Segments** : 160 caractères en GSM-7 (153 par segment au-delà) ; 70 en Unicode (arabe, emojis, 67 par segment au-delà). Le maximum est de 10 segments.

---

## 6. Référence des endpoints

Dans les exemples : `export SMS_API=https://sms.votre-entreprise.ma/api/v1` et `export SMS_KEY=sk_…`

### 6.1 Envoyer un SMS — `POST /messages`

Permission `messages:send`. Réponse **202**.

| Champ | Type | Description |
|---|---|---|
| `to` | string, **requis** | destinataire |
| `message` | string, **requis** | texte (1 600 caractères maximum) |
| `device_id` | uuid | imposer un téléphone (sinon : le téléphone disponible le moins chargé) |
| `sim_slot` | 0 \| 1 | imposer la SIM 1 ou 2 |
| `scheduled_at` | datetime | envoi différé |
| `priority` | 0–10 | les messages prioritaires passent devant (défaut 0) |
| `external_ref` | string | votre référence (commande, facture…), recherchable ensuite |
| `ignore_window` | bool | envoyer même la nuit (clé autorisée uniquement) |

En-tête facultatif `Idempotency-Key` : renvoyer la même requête avec la même clé ne crée pas de doublon. Utilisez-le en cas de nouvelle tentative réseau.

```bash
curl -X POST "$SMS_API/messages" \
  -H "Authorization: Bearer $SMS_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: commande-10045" \
  -d '{"to": "0612345678", "message": "Votre commande 10045 est prête. Merci !", "external_ref": "CMD-10045"}'
```

```json
{
  "data": [{
    "id": "f5c74e7e-bb06-4e79-9fdc-5974a4016931", "direction": "out", "phone": "+212612345678",
    "body": "Votre commande 10045 est prête. Merci !", "status": "queued", "encoding": "gsm7",
    "segments": 1, "priority": 0, "source": "api", "external_ref": "CMD-10045",
    "created_at": "2026-10-21T21:17:31+00:00"
  }],
  "rejected": []
}
```

**Lot** : jusqu'à 1 000 messages différents en une requête. Les lignes invalides sont listées dans `rejected` sans bloquer les autres.

```bash
curl -X POST "$SMS_API/messages" -H "Authorization: Bearer $SMS_KEY" -H "Content-Type: application/json" \
  -d '{"messages": [
        {"to": "0612345678", "message": "Bonjour Salma"},
        {"to": "0700112233", "message": "مرحبا فاطمة", "sim_slot": 1}
      ]}'
```

### 6.2 Statut d'un message — `GET /messages/{id}`

Permission `messages:read`. Renvoie le message et l'historique de ses statuts (`events`).

```bash
curl "$SMS_API/messages/f5c74e7e-bb06-4e79-9fdc-5974a4016931" -H "Authorization: Bearer $SMS_KEY"
```

```json
{"id": "f5c74e7e-…", "status": "delivered", "sent_at": "…", "delivered_at": "…", "attempts": 1,
 "events": [{"status": "queued", "created_at": "…"}, {"status": "dispatched", "details": {"device": "Samsung A14"}},
            {"status": "sent"}, {"status": "delivered"}]}
```

Préférez les [webhooks](#7-webhooks) à l'interrogation répétée.

### 6.3 Lister les messages — `GET /messages`

Permission `messages:read`. Filtres combinables :

- `status` (un ou plusieurs, séparés par des virgules) ;
- `direction` (`out` \| `in`), `phone`, `external_ref` ;
- `campaign_id`, `device_id` ;
- `date_from`, `date_to` (`AAAA-MM-JJ`, heure locale) ;
- `page`, `per_page` (200 au maximum).

```bash
curl "$SMS_API/messages?status=failed&date_from=2026-10-01&per_page=100" -H "Authorization: Bearer $SMS_KEY"
```

```json
{"data": [ … ], "meta": {"page": 1, "per_page": 100, "total": 3, "pages": 1}}
```

### 6.4 Annuler — `POST /messages/{id}/cancel`

Uniquement avant l'envoi (`scheduled`, `queued`, `paused`, `dispatched`). Sinon : 409 `not_cancelable`.

### 6.5 Envoi groupé personnalisé — `POST /messages/bulk`

Permission `messages:send`. Crée une **campagne** avec un message personnalisé par destinataire.

| Champ | Description |
|---|---|
| `message` ou `template_id` | texte commun avec variables `{nom}`, `{prenom}`, `{montant}`, `{date_echeance}`, `{reference}` ou n'importe quelle clé de `variables` |
| `recipients[]` | `{phone, variables?, message?, external_ref?}`, jusqu'à 10 000 ; `message` remplace le texte commun pour ce destinataire |
| `name` | nom de la campagne |
| `device_id`, `sim_slot`, `scheduled_at`, `priority` | comme pour `/messages` |
| `add_stop_footer` | ajoute la mention de désinscription (« STOP au … ») |

Les variables sont formatées automatiquement : `1250.5` donne `1 250,50`, et `2026-11-05` donne `05/11/2026`. Une variable absente est remplacée par une chaîne vide.

```bash
curl -X POST "$SMS_API/messages/bulk" -H "Authorization: Bearer $SMS_KEY" -H "Content-Type: application/json" -d '{
  "name": "Relance échéances novembre",
  "message": "Bonjour {prenom}, votre échéance {reference} de {montant} MAD est due le {date_echeance}.",
  "add_stop_footer": true,
  "recipients": [
    {"phone": "0612345678", "variables": {"prenom": "Salma", "reference": "FAC-101", "montant": 1250.5, "date_echeance": "2026-11-05"}},
    {"phone": "0700112233", "message": "مرحبا فاطمة، نذكركم بموعد أداء القسط."}
  ]}'
```

```json
{"campaign": {"id": "…", "name": "Relance échéances novembre", "status": "running", "total": 2, "queued": 2, "progress": 0},
 "created": 2, "blocked": 0, "rejected": []}
```

### 6.6 Envoi depuis un fichier Excel / CSV — `POST /messages/upload`

Permission `messages:send`. Requête `multipart/form-data`.

| Champ | Description |
|---|---|
| `file` **requis** | `.xlsx` ou `.csv` (séparateur `;` ou `,`), 10 Mo maximum. Voir [samples/import_exemple.xlsx](../samples/import_exemple.xlsx) |
| `message` / `template_id` | texte commun ; facultatif si le fichier a une colonne `Message` |
| `phone_column`, `message_column` | noms des colonnes, détectées automatiquement sinon |
| `mapping` | JSON `{"Nom de colonne": "variable"}` pour renommer des variables |
| `dry_run` | `true` : valider et prévisualiser **sans envoyer** |
| `save_contacts` | `true` : créer ou mettre à jour les contacts (et leurs échéances si Montant, Date échéance et Référence sont présents) |
| `name`, `device_id`, `sim_slot`, `scheduled_at`, `add_stop_footer` | comme pour `/bulk` |

Chaque colonne devient une variable : « Date échéance » donne `{date_echeance}`, « Ville » donne `{ville}`. Les doublons, numéros invalides et désinscrits sont écartés et listés.

```bash
curl -X POST "$SMS_API/messages/upload" -H "Authorization: Bearer $SMS_KEY" \
  -F "file=@import_exemple.xlsx" \
  -F "message=Bonjour {prenom}, échéance {reference} : {montant} MAD le {date_echeance}." \
  -F "dry_run=true"
```

```json
{
  "import_id": "…",
  "columns": ["Nom", "Prénom", "Téléphone", "Montant", "Date échéance", "Référence", "Ville", "Langue", "Message"],
  "summary": {"total": 8, "valid": 5, "invalid": 2, "duplicate": 1, "blacklisted": 0},
  "preview": [{"row": 2, "phone": "+212612345678", "text": "Bonjour Salma, échéance FAC-2026-001 : 1 250,50 MAD le 26/10/2026."}],
  "rejected": [{"row": 7, "phone": "06.12.34.56.78", "status": "duplicate", "error": "Doublon de la ligne 2"}, …],
  "campaign": null,
  "error_report_url": "/api/v1/messages/upload/…/report"
}
```

Le rapport d'erreurs Excel se télécharge avec `GET /messages/upload/{import_id}/report`. Relancez sans `dry_run` pour envoyer.

### 6.7 Campagnes

| Méthode | Endpoint | Permission |
|---|---|---|
| GET | `/campaigns?status=running` | `campaigns:read` |
| GET | `/campaigns/{id}` (compteurs `total`, `queued`, `sent`, `delivered`, `failed`, `blocked`, `progress`) | `campaigns:read` |
| POST | `/campaigns/{id}/pause`, `/resume`, `/cancel` | `campaigns:write` |

```bash
curl -X POST "$SMS_API/campaigns/{id}/pause" -H "Authorization: Bearer $SMS_KEY"
```

### 6.8 SMS reçus — `GET /inbox`

Permission `inbox:read`. Filtres : `phone`, `since` (synchronisation incrémentale), `unread`, `page`, `per_page`.

```bash
curl "$SMS_API/inbox?since=2026-10-21T08:00:00" -H "Authorization: Bearer $SMS_KEY"
```

Pour réagir en temps réel, abonnez-vous plutôt au webhook `sms.received`. Les réponses `STOP` (ou `توقف`) désinscrivent automatiquement l'expéditeur.

### 6.9 Contacts et groupes

| Méthode | Endpoint | Permission | Notes |
|---|---|---|---|
| GET | `/contacts?q=&group_id=&consent_status=` | `contacts:read` | recherche par nom ou numéro |
| POST | `/contacts` | `contacts:write` | 409 si le numéro existe déjà |
| GET / PATCH / DELETE | `/contacts/{id}` | read / write | DELETE efface aussi les messages : droit à l'effacement, loi 09-08 |
| GET | `/contacts/{id}/export` | `contacts:read` | toutes les données du contact : droit d'accès |
| GET / POST | `/groups` | read / write | |
| POST | `/groups/{id}/contacts` | `contacts:write` | `{"contacts": ["uuid" ou "0612…"]}` ; les numéros inconnus sont créés |

```bash
curl -X POST "$SMS_API/contacts" -H "Authorization: Bearer $SMS_KEY" -H "Content-Type: application/json" -d '{
  "phone": "0612345678", "first_name": "Salma", "last_name": "Alaoui", "locale": "fr",
  "groups": ["Clients"], "consent_status": "opt_in", "custom_fields": {"ville": "Casablanca", "agence": "Maarif"}}'
```

`custom_fields` est utilisable comme variable dans les messages (`{ville}`). `locale` (`fr` \| `ar`) choisit le modèle des automatisations.

### 6.10 Modèles — `GET/POST /templates`, `GET /templates/{id}`

```bash
curl -X POST "$SMS_API/templates" -H "Authorization: Bearer $SMS_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Rappel", "category": "rappel", "language": "ar", "body": "مرحبا {prenom}، موعدكم غدا."}'
```

La réponse contient `variables` (`["prenom"]`) et `sms` (encodage et nombre de segments).

### 6.11 Planifications — `GET/POST /schedules`, `GET/DELETE /schedules/{id}`

| Champ | Description |
|---|---|
| `name`, `body` ou `template_id` | contenu |
| `audience` | `{"groups": [1, 2], "contacts": ["uuid"], "exclude_groups": [], "all": false}` |
| `recurrence` | `once` (avec `run_date`) \| `daily` \| `weekly` (avec `days_of_week`, 1 = lundi) \| `monthly` (avec `day_of_month` ; 31 = dernier jour) |
| `run_time` | `"09:30"`, heure de Casablanca |
| `start_date`, `end_date`, `device_id`, `sim_slot`, `add_stop_footer` | facultatifs |

```bash
curl -X POST "$SMS_API/schedules" -H "Authorization: Bearer $SMS_KEY" -H "Content-Type: application/json" -d '{
  "name": "Rappel mensuel", "body": "Bonjour {prenom}, pensez à votre règlement mensuel.",
  "audience": {"groups": [1]}, "recurrence": "monthly", "day_of_month": 1, "run_time": "10:00"}'
```

La réponse contient `next_run_at`. Chaque exécution crée une campagne.

### 6.12 Appareils — `GET /devices`, `GET /devices/{id}`

Permission `devices:read`. La réponse donne :

- l'état (`online` \| `offline` \| `disabled`), la batterie, la charge, le signal, la file locale et `last_seen_at` ;
- les SIM (`slot`, `carrier`, `phone_number`) ;
- les limites (`send_interval_sec`, `max_per_hour`, `max_per_day`).

---

## 7. Webhooks

Recevez les événements en temps réel sur votre URL HTTPS publique. Créez-la dans le tableau de bord (**API & webhooks**), ou par l'API (`webhooks:manage`) :

```bash
curl -X POST "$SMS_API/webhooks" -H "Authorization: Bearer $SMS_KEY" -H "Content-Type: application/json" \
  -d '{"url": "https://exemple.ma/hooks/sms", "events": ["sms.delivered", "sms.failed", "sms.received"]}'
```

La réponse contient `secret` (`whsec_…`), **affiché une seule fois**.

| Événement | Quand |
|---|---|
| `sms.sent` | le téléphone a remis le SMS à l'opérateur |
| `sms.delivered` | accusé de livraison reçu |
| `sms.failed` | échec définitif |
| `sms.received` | SMS entrant |
| `device.offline` / `device.online` | un téléphone se déconnecte (plus de 90 s sans heartbeat) ou revient |

Requête envoyée (`POST`, JSON) :

```http
X-Webhook-Event: sms.delivered
X-Webhook-Id: 3f0c…              (identique entre les tentatives : dédoublonnez avec)
X-Webhook-Timestamp: 1792617454
X-Webhook-Signature: sha256=5d1c…
```

```json
{
  "id": "3f0c…", "event": "sms.delivered", "created_at": "2026-10-21T21:17:34+00:00",
  "data": {"id": "f5c74e7e-…", "direction": "out", "phone": "+212612345678", "body": "…", "status": "delivered",
           "segments": 1, "external_ref": "CMD-10045", "campaign_id": null, "device_id": "…", "sim_slot": 0,
           "sent_at": "…", "delivered_at": "…", "error_code": null}
}
```

- **Signature** : `HMAC-SHA256(secret, "<X-Webhook-Timestamp>.<corps brut>")` en hexadécimal. Vérifiez-la sur le **corps brut**, avant tout décodage JSON. Refusez les horodatages de plus de 5 minutes (anti-rejeu). Exemples ci-dessous.
- **Réponse attendue** : un code 2xx en moins de 10 s. Traitez le reste en arrière-plan.
- **Nouvelles tentatives** : après 30 s, 2 min, 10 min, 1 h puis 6 h (6 tentatives en tout). Le webhook est désactivé après 50 échecs consécutifs (notification aux administrateurs). Le journal des livraisons est consultable dans le tableau de bord.

---

## 8. Exemples complets

### Python

`pip install requests`. Testé avec Python 3.10 et plus.

```python
"""Client minimal SMS Gateway."""
import hashlib
import hmac
import time
import uuid

import requests

API = "https://sms.votre-entreprise.ma/api/v1"
KEY = "sk_..."


class SmsGatewayError(Exception):
    def __init__(self, status, error):
        super().__init__(f"{status} {error.get('code')}: {error.get('message')}")
        self.status, self.code, self.details = status, error.get("code"), error.get("details")


class SmsGateway:
    def __init__(self, api=API, key=KEY, timeout=30):
        self.api, self.timeout = api.rstrip("/"), timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {key}"

    def _request(self, method, path, retries=3, **kwargs):
        for attempt in range(retries):
            r = self.session.request(method, self.api + path, timeout=self.timeout, **kwargs)
            if r.status_code == 429 and attempt < retries - 1:   # limite atteinte : on patiente
                time.sleep(int(r.headers.get("Retry-After", "5")))
                continue
            if r.status_code >= 400:
                raise SmsGatewayError(r.status_code, r.json().get("error", {}) if r.content else {})
            return r.json() if r.content else None

    def send(self, to, message, idempotency_key=None, **options):
        headers = {"Idempotency-Key": idempotency_key or str(uuid.uuid4())}
        return self._request("POST", "/messages", json={"to": to, "message": message, **options},
                             headers=headers)["data"][0]

    def send_batch(self, messages):
        return self._request("POST", "/messages", json={"messages": messages})

    def bulk(self, message, recipients, **options):
        return self._request("POST", "/messages/bulk", json={"message": message, "recipients": recipients, **options})

    def upload(self, path, message=None, dry_run=False, **options):
        with open(path, "rb") as f:
            data = {"message": message or "", "dry_run": str(dry_run).lower(), **{k: str(v) for k, v in options.items()}}
            return self._request("POST", "/messages/upload", files={"file": f}, data=data)

    def status(self, message_id):
        return self._request("GET", f"/messages/{message_id}")

    def inbox(self, since=None):
        return self._request("GET", "/inbox", params={"since": since} if since else None)["data"]

    def devices(self):
        return self._request("GET", "/devices")


def verify_webhook(secret: str, body: bytes, timestamp: str, signature: str, tolerance=300) -> bool:
    """À appeler avec le corps BRUT de la requête."""
    if abs(time.time() - int(timestamp)) > tolerance:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


if __name__ == "__main__":
    gw = SmsGateway()
    msg = gw.send("0612345678", "Votre code de confirmation : 482913", external_ref="OTP-1", priority=10)
    print("Envoyé :", msg["id"], msg["status"])

    campaign = gw.bulk("Bonjour {prenom}, {montant} MAD dus le {date_echeance}.", [
        {"phone": "0612345678", "variables": {"prenom": "Salma", "montant": 1250.5, "date_echeance": "2026-11-05"}},
        {"phone": "0700112233", "variables": {"prenom": "Omar", "montant": 300, "date_echeance": "2026-11-07"}},
    ], name="Relances", add_stop_footer=True)
    print("Campagne :", campaign["campaign"]["id"], campaign["created"], "messages")

    report = gw.upload("import_exemple.xlsx", "Bonjour {prenom}, échéance {reference}.", dry_run=True)
    print("Import :", report["summary"])

    for m in gw.inbox():
        print("Reçu de", m["phone"], ":", m["body"])
```

Réception des webhooks avec Flask :

```python
from flask import Flask, abort, request

app = Flask(__name__)
SECRET = "whsec_..."
seen = set()   # en production : table en base, avec contrainte unique sur X-Webhook-Id


@app.post("/hooks/sms")
def sms_webhook():
    body = request.get_data()   # corps brut, avant décodage JSON
    if not verify_webhook(SECRET, body, request.headers.get("X-Webhook-Timestamp", "0"),
                          request.headers.get("X-Webhook-Signature", "")):
        abort(401)
    if request.headers["X-Webhook-Id"] in seen:   # nouvelle tentative déjà traitée
        return "", 200
    seen.add(request.headers["X-Webhook-Id"])
    event = request.get_json()
    if event["event"] == "sms.delivered":
        print("Livré :", event["data"]["external_ref"])
    elif event["event"] == "sms.received":
        print("Réponse de", event["data"]["phone"], ":", event["data"]["body"])
    return "", 204
```

### PHP

PHP 8.0 ou plus, extension `curl` (aucune dépendance Composer).

```php
<?php
// Client minimal SMS Gateway
final class SmsGateway
{
    public function __construct(
        private string $api = 'https://sms.votre-entreprise.ma/api/v1',
        private string $key = 'sk_...'
    ) {}

    private function request(string $method, string $path, $body = null, array $headers = [], bool $multipart = false): array
    {
        $ch = curl_init(rtrim($this->api, '/') . $path);
        $headers[] = 'Authorization: Bearer ' . $this->key;
        $headers[] = 'Accept: application/json';
        if ($body !== null && !$multipart) {
            $body = json_encode($body, JSON_UNESCAPED_UNICODE);
            $headers[] = 'Content-Type: application/json; charset=utf-8';
        }
        curl_setopt_array($ch, [
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_TIMEOUT => 30,
        ]);
        if ($body !== null) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
        }
        $raw = curl_exec($ch);
        if ($raw === false) {
            throw new RuntimeException('Erreur réseau : ' . curl_error($ch));
        }
        $status = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
        curl_close($ch);
        $data = $raw === '' ? [] : json_decode($raw, true);
        if ($status >= 400) {
            $err = $data['error'] ?? [];
            throw new RuntimeException("$status {$err['code']}: {$err['message']}", $status);
        }
        return $data ?? [];
    }

    public function send(string $to, string $message, array $options = [], ?string $idempotencyKey = null): array
    {
        $headers = $idempotencyKey ? ["Idempotency-Key: $idempotencyKey"] : [];
        return $this->request('POST', '/messages', ['to' => $to, 'message' => $message] + $options, $headers)['data'][0];
    }

    public function bulk(string $message, array $recipients, array $options = []): array
    {
        return $this->request('POST', '/messages/bulk', ['message' => $message, 'recipients' => $recipients] + $options);
    }

    public function upload(string $file, string $message = '', bool $dryRun = false): array
    {
        return $this->request('POST', '/messages/upload', [
            'file' => new CURLFile($file),
            'message' => $message,
            'dry_run' => $dryRun ? 'true' : 'false',
        ], [], true);
    }

    public function status(string $id): array
    {
        return $this->request('GET', '/messages/' . rawurlencode($id));
    }

    public function inbox(?string $since = null): array
    {
        return $this->request('GET', '/inbox' . ($since ? '?since=' . rawurlencode($since) : ''))['data'];
    }
}

// Vérification d'un webhook (à placer en tête du script qui reçoit l'appel)
function sms_webhook_is_valid(string $secret, string $rawBody, string $timestamp, string $signature): bool
{
    if (abs(time() - (int) $timestamp) > 300) {
        return false;
    }
    $expected = 'sha256=' . hash_hmac('sha256', $timestamp . '.' . $rawBody, $secret);
    return hash_equals($expected, $signature);
}

// --- Utilisation ---
$gw = new SmsGateway();
$msg = $gw->send('0612345678', 'Votre commande 10045 est prête.', ['external_ref' => 'CMD-10045'], 'cmd-10045');
echo "Envoyé : {$msg['id']} ({$msg['status']})\n";

$res = $gw->bulk('Bonjour {prenom}, {montant} MAD dus le {date_echeance}.', [
    ['phone' => '0612345678', 'variables' => ['prenom' => 'Salma', 'montant' => 1250.5, 'date_echeance' => '2026-11-05']],
]);
echo "Campagne : {$res['campaign']['id']}\n";

// webhook.php
// $raw = file_get_contents('php://input');
// if (!sms_webhook_is_valid('whsec_...', $raw, $_SERVER['HTTP_X_WEBHOOK_TIMESTAMP'] ?? '0', $_SERVER['HTTP_X_WEBHOOK_SIGNATURE'] ?? '')) {
//     http_response_code(401); exit;
// }
// $event = json_decode($raw, true);
// http_response_code(204);
```

### JavaScript (Node.js)

Node 18 ou plus : `fetch`, `FormData` et `Blob` sont natifs, sans dépendance.

```javascript
// Client minimal SMS Gateway (ESM : enregistrez en .mjs)
import crypto from "node:crypto";
import { readFile } from "node:fs/promises";

const API = "https://sms.votre-entreprise.ma/api/v1";
const KEY = "sk_...";

async function request(method, path, { json, form, headers = {} } = {}) {
  const res = await fetch(API + path, {
    method,
    headers: { Authorization: `Bearer ${KEY}`, ...(json ? { "Content-Type": "application/json" } : {}), ...headers },
    body: json ? JSON.stringify(json) : form,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = new Error(`${res.status} ${data?.error?.code}: ${data?.error?.message}`);
    Object.assign(err, { status: res.status, code: data?.error?.code, details: data?.error?.details });
    throw err;
  }
  return data;
}

export const sms = {
  send: (to, message, options = {}, idempotencyKey = crypto.randomUUID()) =>
    request("POST", "/messages", { json: { to, message, ...options }, headers: { "Idempotency-Key": idempotencyKey } })
      .then((r) => r.data[0]),
  bulk: (message, recipients, options = {}) =>
    request("POST", "/messages/bulk", { json: { message, recipients, ...options } }),
  upload: async (path, message = "", dryRun = false) => {
    const form = new FormData();
    form.append("file", new Blob([await readFile(path)]), path.split(/[\\/]/).pop());
    form.append("message", message);
    form.append("dry_run", String(dryRun));
    return request("POST", "/messages/upload", { form });
  },
  status: (id) => request("GET", `/messages/${id}`),
  inbox: (since) => request("GET", "/inbox" + (since ? `?since=${encodeURIComponent(since)}` : "")).then((r) => r.data),
  devices: () => request("GET", "/devices"),
};

/** Vérifie un webhook : rawBody est le corps BRUT (Buffer ou chaîne). */
export function verifyWebhook(secret, rawBody, timestamp, signature, toleranceSec = 300) {
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > toleranceSec) return false;
  const expected = "sha256=" + crypto.createHmac("sha256", secret).update(`${timestamp}.`).update(rawBody).digest("hex");
  const a = Buffer.from(expected), b = Buffer.from(signature || "");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// --- Utilisation ---
const msg = await sms.send("0612345678", "Votre rendez-vous est confirmé pour demain 10h.", { external_ref: "RDV-88" });
console.log("Envoyé :", msg.id, msg.status);
const report = await sms.upload("import_exemple.xlsx", "Bonjour {prenom}, échéance {reference}.", true);
console.log("Import :", report.summary);
```

Réception avec Express (le corps doit rester brut pour la signature) :

```javascript
import express from "express";
const app = express();
app.post("/hooks/sms", express.raw({ type: "application/json" }), (req, res) => {
  if (!verifyWebhook("whsec_...", req.body, req.get("X-Webhook-Timestamp"), req.get("X-Webhook-Signature"))) {
    return res.sendStatus(401);
  }
  const event = JSON.parse(req.body);
  if (event.event === "sms.received") console.log("Réponse de", event.data.phone, ":", event.data.body);
  res.sendStatus(204);
});
app.listen(3000);
```

---

## 9. Bonnes pratiques

- Mettez un **`Idempotency-Key`** sur chaque envoi unitaire : une nouvelle tentative après un délai réseau ne crée pas de doublon.
- Renseignez **`external_ref`** avec votre identifiant métier, pour retrouver un message et le relier aux webhooks.
- Pour plus de 50 destinataires, utilisez **`/messages/bulk`** ou **`/messages/upload`** plutôt qu'une boucle d'appels : vous obtenez une campagne pilotable et un seul appel.
- Testez les imports avec **`dry_run=true`** avant l'envoi réel.
- Pour les SMS commerciaux, utilisez **`add_stop_footer`** et ne ciblez que des contacts consentants (loi 09-08).
- Les envois respectent la **fenêtre horaire**, 08:00–21:00 par défaut, et les limites de chaque téléphone. Un message `queued` peut donc partir plus tard : c'est normal.
- Une clé par application, avec les permissions minimales nécessaires. Faites-les tourner régulièrement.

## 10. Protocole des téléphones (référence interne)

Utilisé par l'application Android, mais pas par les intégrations.

| Canal | Détail |
|---|---|
| Appairage | `POST /device/v1/pair` `{code, model, android_version, sims[]}` → `{token, device}` |
| Socket.IO `/device` | `auth: {token}`. Serveur → téléphone : `task.send {messages}`, `task.cancel {ids}`, `config.update`, `device.revoked`. Téléphone → serveur, avec ACK : `heartbeat`, `task.ack {ids}`, `sms.status {statuses}`, `sms.received {messages}` |
| Repli HTTP | `Authorization: Bearer dev_…` : `GET /device/v1/tasks`, `POST /device/v1/tasks/ack`, `POST /device/v1/status`, `POST /device/v1/inbound`, `POST /device/v1/heartbeat`, `GET /device/v1/config` |
