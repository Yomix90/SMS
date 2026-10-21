# SMS Gateway : architecture et schéma de base de données

> Étape 1 sur 8, à valider avant la génération du code.
> Choix retenus : WebSocket (Socket.IO) avec repli en polling HTTP, une seule organisation, 1 à 5 téléphones, front Jinja + Bootstrap 5.

---

## 1. Vue d'ensemble

```
                          Internet (HTTPS 443)
                                  │
                       ┌──────────▼───────────┐
                       │  Nginx (TLS, LE)     │  reverse proxy, upgrade WebSocket,
                       │  certbot (renouvel.) │  limites de taille, en-têtes sécurité
                       └──────────┬───────────┘
                                  │ :8000
   ┌──────────────────────────────▼────────────────────────────────────┐
   │  Conteneur "web" : Gunicorn (1 worker gevent) + Flask             │
   │                                                                   │
   │  Blueprints                Services                 Tâches (APScheduler)
   │  • web (Jinja, FR/AR)      • phone (normalisation)  • dispatcher (5 s)
   │  • api/v1 (clé API)        • sms_encoding (GSM/UCS2)• retry échecs (30 s)
   │  • device/v1 (token app)   • templating (fusion)    • détection offline (30 s)
   │  • socket.io /device,      • importer (openpyxl)    • planifications (60 s)
   │    /dashboard              • dispatcher, webhooks   • automatisations (5 min)
   │                            • keywords, reports      • webhooks retry (30 s)
   └───────┬───────────────────────────────────────────────────────────┘
           │ SQLAlchemy 2
   ┌───────▼────────┐        ┌────────────────────┐
   │ PostgreSQL 16  │        │ Conteneur "backup" │  pg_dump quotidien, rotation 14 j
   │ (SQLite en dev)│◄───────┤                    │
   └────────────────┘        └────────────────────┘
           ▲
           │ Socket.IO (wss) : tâches poussées, statuts, heartbeat 30 s
           │ HTTP (repli)    : GET tasks / POST status / POST inbound
   ┌───────┴──────────────────────────────────────────┐
   │  Téléphone Android (APK direct)                  │
   │  GatewayService (foreground, type remoteMessaging)│
   │  • SmsSender (SmsManager multipart, double SIM)  │
   │  • SmsReceiver (SMS entrants)                    │
   │  • Room : file locale + boîte d'envoi des statuts│
   │  • RateLimiter local, BootReceiver, WorkManager  │
   └──────────────────────────────────────────────────┘
```

### Pourquoi ces choix
| Sujet | Choix | Justification |
|---|---|---|
| Temps réel | **Flask-SocketIO** (namespaces `/device` et `/dashboard`) | Un seul protocole pour le téléphone (`socket.io-client` Java) et le navigateur ; reconnexion automatique intégrée. |
| Repli | Polling HTTP `device/v1/*` | Si le WebSocket tombe (réseau mobile, proxy), l'app continue en polling toutes les 15 s. |
| Process | **1 worker Gunicorn gevent** + APScheduler dans le même process | Suffisant pour 1 à 5 téléphones, sans Redis. Un verrou en base (`scheduler_locks`) évite les doublons si on scale plus tard. Évolution possible vers Redis + `message_queue` Socket.IO. |
| File d'attente | **Table `messages`** (statut + `next_attempt_at`) | Pas de broker : la base est la source de vérité, ce qui donne une file transactionnelle et observable. |
| API + OpenAPI | **flask-smorest** + marshmallow | Validation stricte des entrées et Swagger UI générés à partir des mêmes schémas. |
| i18n | **Flask-Babel** (fr, ar) + Bootstrap 5.3 RTL | `dir="rtl"` et la feuille `bootstrap.rtl.min.css` sont choisis selon la langue. Mode sombre via `data-bs-theme`. |
| Sécurité | argon2-cffi, Flask-WTF (CSRF), Flask-Limiter, `cryptography` (Fernet) | Voir §5. |
| Téléphones | lib `phonenumbers` + règles marocaines | `06…`/`07…` → `+2126…`/`+2127…`, `00212`, `212`, espaces, points, tirets. Les fixes `05…` sont refusés pour le SMS. |
| PDF / Excel | reportlab / openpyxl | Légers, purs Python. |
| Android | Kotlin, minSdk 26 (Android 8), targetSdk 35 | Couvre ~97 % du parc ; foreground service de type `remoteMessaging` (Android 14+), qui n'a pas la limite de 6 h de `dataSync` sous Android 15. |

---

## 2. Flux principaux

### 2.1 Appairage par QR code
1. L'admin clique sur **Ajouter un appareil**. Le serveur crée un `pairing_code` (32 octets aléatoires, valable 10 min, usage unique ; seul le hash est stocké).
2. Le tableau de bord affiche un QR contenant `{"v":1,"url":"https://sms.mondomaine.ma","code":"<code>"}`.
3. L'app scanne le QR puis envoie `POST /device/v1/pair` avec le code, le modèle, la version Android et la liste des SIM.
4. Le serveur crée le `device` et ses `device_sims`, génère un **token d'appareil** (`dev_` + 40 caractères, renvoyé une seule fois, stocké haché en SHA-256) et marque le code comme utilisé.
5. L'app stocke le token dans le Keystore (EncryptedSharedPreferences), ouvre le WebSocket, et l'appareil apparaît en ligne sur le tableau de bord en direct.
6. Rotation : bouton **Régénérer le token** (le téléphone doit être réappairé) ; révocation immédiate possible.

### 2.2 Cycle de vie d'un SMS sortant
```
draft ─► queued ─► dispatched ─► sent ─► delivered
            │           │          └────► failed ──(retryable & attempts<max)──► queued
            │           └─(pas d'ACK en 2 min)──► queued
            ├─► scheduled (scheduled_at futur ou hors fenêtre horaire) ─► queued
            └─► canceled / blocked (liste noire, STOP, quota)
```
- **Dispatcher** (toutes les 5 s) : sélectionne les messages `queued` dont `next_attempt_at <= now`, vérifie la fenêtre horaire (Africa/Casablanca), la liste noire et les quotas, puis choisit l'appareil (imposé, sinon celui en ligne le moins chargé dont le budget de débit n'est pas épuisé) et pousse le lot via Socket.IO (`task.send`). Le message passe en `dispatched`.
- **Téléphone** : enregistre le lot dans Room (idempotent par `uuid`), envoie un `task.ack`, puis envoie les SMS au rythme de son propre limiteur (délai entre SMS, max/heure, max/jour). Les `PendingIntent` sent/delivered remontent `sms.status`.
- **Retry** : les codes retryables (`RESULT_ERROR_GENERIC_FAILURE`, `NO_SERVICE`, `RADIO_OFF`, timeout) sont relancés avec un backoff de 1, 5 puis 15 min, éventuellement sur un autre appareil, jusqu'à `max_attempts` (paramétrable, 3 par défaut). Les erreurs définitives (`NULL_PDU`, numéro invalide) passent directement en `failed` et déclenchent une notification.
- Chaque transition est historisée dans `message_events` et peut déclencher un webhook.

### 2.3 SMS entrant
Le téléphone reçoit le SMS (multipart réassemblé) et l'enregistre dans Room avec un `uuid` local, puis l'envoie au serveur (`sms.received`, avec repli HTTP). Côté serveur :
1. Dédoublonnage par `device_uuid`, rattachement au contact et à la conversation.
2. Mots-clés STOP (`STOP`, `ARRET`, `ARRÊT`, `توقف`, `ستوب`, `إيقاف`) : ajout en liste noire et confirmation.
3. Réponses automatiques par mots-clés.
4. Diffusion temps réel au tableau de bord (`/dashboard`) et webhook `sms.received`.

### 2.4 Heartbeat et état en ligne
L'app envoie un `heartbeat` toutes les 30 s avec la batterie, l'état de charge, le signal, la taille de sa file et les compteurs. Sans heartbeat depuis 90 s, l'appareil passe hors ligne : notification, webhook `device.offline`, et ses messages `dispatched` sans ACK sont remis en file.

---

## 3. Schéma de base de données

Conventions : `id` en BIGINT auto-incrémenté (clé interne) et `uuid` public exposé dans l'API (pas d'énumération d'IDs). Toutes les dates sont stockées en **UTC** (`timestamptz`) et affichées en Africa/Casablanca. Le type `JSON` correspond à JSONB sous PostgreSQL et à JSON sous SQLite. Les migrations sont gérées par Alembic (Flask-Migrate).

### 3.1 Utilisateurs, sécurité, audit
**users**
| Colonne | Type | Contraintes / notes |
|---|---|---|
| id | bigint | PK |
| email | varchar(255) | UNIQUE, NOT NULL, stocké en minuscules |
| password_hash | varchar(255) | argon2id |
| full_name | varchar(120) | |
| role | varchar(20) | `admin` \| `operator` |
| locale | varchar(5) | `fr` \| `ar`, défaut `fr` |
| theme | varchar(10) | `light` \| `dark` \| `auto` |
| is_active | bool | défaut true |
| failed_logins | int | verrouillage temporaire après 5 échecs |
| locked_until | timestamptz | null |
| last_login_at | timestamptz | |
| created_at, updated_at | timestamptz | |

**audit_logs** : `id`, `user_id` FK→users (null), `api_key_id` FK→api_keys (null), `action` varchar(60) (ex. `campaign.start`, `device.revoke`, `user.login_failed`), `entity_type` varchar(40), `entity_id` bigint, `ip` varchar(45), `user_agent` varchar(255), `details` JSON, `created_at`. Index sur (`created_at`) et (`entity_type`, `entity_id`).

**api_keys**
| Colonne | Type | Notes |
|---|---|---|
| id | bigint | PK |
| uuid | uuid | UNIQUE |
| name | varchar(100) | |
| prefix | varchar(12) | ex. `sk_live_ab12` (affiché pour l'identification) |
| key_hash | char(64) | SHA-256 de la clé complète, UNIQUE |
| scopes | JSON | ex. `["messages:send","messages:read","contacts:write","inbox:read","devices:read","webhooks:manage"]` |
| rate_limit_per_min | int | défaut 60 |
| daily_quota | int | SMS/jour, null = illimité |
| allow_outside_window | bool | autorise l'envoi transactionnel hors fenêtre horaire |
| expires_at | timestamptz | null = jamais |
| revoked_at | timestamptz | |
| last_used_at, last_used_ip | | |
| created_by | FK→users | |
| created_at | timestamptz | |

**settings** : `key` varchar(80) PK, `value` JSON, `updated_by`, `updated_at`. Clés prévues : `send_window` (`{"start":"08:00","end":"21:00","days":[1,2,3,4,5,6,7]}`), `timezone`, `max_attempts`, `retry_backoff`, `stop_footer` (`{"fr":"STOP au {numero}","ar":"..."}`), `stop_keywords`, `abuse_threshold` (ex. >500 SMS en 10 min → pause et alerte), `default_country`.

**notifications** (in-app) : `id`, `user_id` FK (null = tous les admins), `type` (`message.failed`, `device.offline`, `abuse.detected`, `import.done`…), `title`, `body`, `link`, `is_read`, `created_at`.

**scheduler_locks** : `name` PK, `locked_until`, `owner` (pour éviter la double exécution si plusieurs process).

### 3.2 Appareils
**devices**
| Colonne | Type | Notes |
|---|---|---|
| id | bigint | PK |
| uuid | uuid | UNIQUE |
| name | varchar(100) | ex. « Samsung A14 – Agence Casa » |
| token_hash | char(64) | UNIQUE, SHA-256 |
| token_prefix | varchar(12) | |
| model, manufacturer, android_version, app_version | varchar | |
| status | varchar(15) | `online` \| `offline` \| `disabled` |
| battery_level | smallint | 0–100 |
| is_charging | bool | |
| signal_level | smallint | 0–4 |
| local_queue_size | int | |
| last_seen_at | timestamptz | |
| last_ip | varchar(45) | |
| default_sim_slot | smallint | 0/1 |
| send_interval_sec | int | défaut 5 (délai mini entre deux SMS) |
| max_per_hour | int | défaut 100 |
| max_per_day | int | défaut 500 |
| is_enabled | bool | |
| revoked_at | timestamptz | |
| created_by, created_at, updated_at | | |

**device_sims** : `id`, `device_id` FK (CASCADE), `slot_index` smallint, `subscription_id` int, `carrier` (IAM, Orange, inwi), `phone_number` (si lisible), `is_active`, `sent_today`, `counter_date`. Contrainte UNIQUE (`device_id`, `slot_index`).

**pairing_codes** : `id`, `code_hash` char(64) UNIQUE, `expires_at`, `used_at`, `device_id` FK (rempli après appairage), `created_by` FK→users, `created_at`.

### 3.3 Contacts et conformité
**contacts**
| Colonne | Type | Notes |
|---|---|---|
| id | bigint | PK |
| uuid | uuid | UNIQUE |
| phone | varchar(20) | **E.164** (`+2126…`), UNIQUE |
| first_name, last_name | varchar(80) | variables `{prenom}`, `{nom}` |
| email | varchar(255) | |
| birthday | date | trigger anniversaire |
| locale | varchar(5) | langue préférée du client (choix du modèle FR/AR) |
| custom_fields | JSON | toute autre colonne importée → `{n'importe_quelle_colonne}` |
| consent_status | varchar(15) | `opt_in` \| `unknown` \| `opted_out` |
| consent_source | varchar(40) | `import`, `formulaire`, `api`, `sms` |
| consent_at | timestamptz | preuve de consentement (loi 09-08) |
| opted_out_at | timestamptz | |
| notes | text | |
| created_at, updated_at | | |

Index : (`last_name`, `first_name`) ; GIN sur `custom_fields` (PostgreSQL).

**groups** : `id`, `name` UNIQUE, `description`, `color`, `created_at`.
**contact_groups** : `contact_id` FK, `group_id` FK, PK composite.

**blacklist** : `id`, `phone` E.164 UNIQUE, `reason` (`stop_keyword`, `manuel`, `plainte`, `invalide`), `source_message_id` FK→messages (null), `created_by` (null si automatique), `created_at`. Elle est séparée de `contacts` pour pouvoir bloquer un numéro qui n'est pas un contact. Le dispatcher la vérifie avant **chaque** envoi.

**receivables** (échéances et impayés : alimentent `{montant}`, `{date_echeance}`, `{reference}` et les relances)
| Colonne | Type | Notes |
|---|---|---|
| id | bigint | PK |
| contact_id | FK→contacts | CASCADE |
| reference | varchar(60) | UNIQUE avec contact_id |
| amount | numeric(12,2) | |
| currency | char(3) | défaut `MAD` |
| due_date | date | |
| status | varchar(12) | `unpaid` \| `paid` \| `canceled` |
| paid_at | timestamptz | |
| extra | JSON | |
| created_at, updated_at | | |

Index sur (`status`, `due_date`).

### 3.4 Modèles, campagnes, imports
**templates** : `id`, `uuid`, `name`, `category` (`rappel`, `relance`, `promo`, `bienvenue`, `transactionnel`…), `language` (`fr`\|`ar`), `body` text, `variables` JSON (extraites automatiquement), `is_active`, `created_by`, `created_at`, `updated_at`. UNIQUE (`name`, `language`).

**imports**
| Colonne | Type | Notes |
|---|---|---|
| id | bigint | PK |
| uuid | uuid | |
| original_filename | varchar(255) | |
| stored_path | varchar(255) | hors webroot, supprimé après 30 j |
| sheet_name | varchar(100) | |
| header_row | int | |
| columns | JSON | colonnes détectées |
| mapping | JSON | `{"Téléphone":"phone","Nom":"nom","Montant":"montant","Message":"message",...}` |
| status | varchar(15) | `uploaded` → `mapped` → `validated` → `sent` \| `failed` |
| total_rows, valid_rows, invalid_rows, duplicate_rows | int | |
| save_contacts | bool | créer ou mettre à jour les contacts |
| error_report_path | varchar(255) | xlsx téléchargeable |
| created_by, created_at | | |

**import_rows** : `id`, `import_id` FK (CASCADE), `row_number`, `raw` JSON, `phone_raw`, `phone_e164`, `variables` JSON, `custom_message` text (colonne « message »), `status` (`valid`\|`invalid`\|`duplicate`\|`blacklisted`), `error` varchar(255), `contact_id` FK (null). Index (`import_id`, `status`).

**campaigns**
| Colonne | Type | Notes |
|---|---|---|
| id | bigint | PK |
| uuid | uuid | |
| name | varchar(150) | |
| body | text | texte avec variables (copie figée du modèle) |
| template_id | FK→templates | null |
| source | varchar(15) | `manual` \| `import` \| `api` \| `schedule` \| `automation` |
| import_id | FK→imports | null |
| audience | JSON | `{"groups":[1,2],"contacts":[...],"exclude_groups":[]}` |
| device_id | FK→devices | null = répartition automatique |
| sim_slot | smallint | null = SIM par défaut de l'appareil |
| status | varchar(12) | `draft` \| `scheduled` \| `running` \| `paused` \| `completed` \| `canceled` |
| scheduled_at | timestamptz | |
| add_stop_footer | bool | |
| total, queued, sent, delivered, failed, blocked | int | compteurs dénormalisés |
| started_at, completed_at | timestamptz | |
| created_by, created_at, updated_at | | |

La pause d'une campagne remet ses messages `queued` en `paused` ; la reprise les remet en `queued` ; l'annulation les passe en `canceled`.

### 3.5 Messages (file d'attente + historique + boîte de réception)
Une **table unique** `messages` regroupe les SMS sortants et entrants (`direction`). Une conversation se lit ainsi en une seule requête triée.

**messages**
| Colonne | Type | Notes |
|---|---|---|
| id | bigint | PK |
| uuid | uuid | UNIQUE, ID public API et ID transmis au téléphone |
| direction | varchar(3) | `out` \| `in` |
| conversation_id | FK→conversations | |
| contact_id | FK→contacts | null |
| phone | varchar(20) | E.164 |
| body | text | texte final rendu (variables fusionnées) |
| encoding | varchar(5) | `gsm7` \| `ucs2` |
| segments | smallint | |
| status | varchar(12) | out : `scheduled`, `queued`, `paused`, `dispatched`, `sent`, `delivered`, `failed`, `canceled`, `blocked` ; in : `received` |
| priority | smallint | 0 = normal, 10 = transactionnel / API prioritaire |
| campaign_id | FK→campaigns | null |
| device_id | FK→devices | null (assigné au dispatch) |
| device_pinned | bool | *ajouté à l'implémentation* : appareil imposé par l'utilisateur ; sinon une relance peut partir d'un autre téléphone |
| sim_slot | smallint | |
| scheduled_at | timestamptz | |
| next_attempt_at | timestamptz | moment où le dispatcher pourra le prendre |
| attempts | smallint | |
| max_attempts | smallint | |
| dispatched_at, sent_at, delivered_at, failed_at, received_at | timestamptz | |
| acked_at | timestamptz | *ajouté à l'implémentation* : accusé du téléphone (lot bien stocké dans Room) ; sans accusé après 2 min, le message est remis en file |
| error_code | varchar(40) | ex. `GENERIC_FAILURE`, `NO_SERVICE`, `INVALID_NUMBER` |
| error_message | varchar(255) | |
| ignore_window | bool | envoi transactionnel hors fenêtre (permission requise) |
| source | varchar(15) | `web`, `api`, `campaign`, `schedule`, `automation`, `auto_reply`, `device` |
| user_id | FK→users | null |
| api_key_id | FK→api_keys | null |
| external_ref | varchar(100) | référence fournie par le client API |
| idempotency_key | varchar(100) | UNIQUE (`api_key_id`, `idempotency_key`) : évite les doubles envois |
| device_uuid | varchar(64) | dédoublonnage des entrants, UNIQUE (`device_id`, `device_uuid`) |
| is_read | bool | entrants |
| created_at | timestamptz | |

Index :
- `ix_msg_queue` (`status`, `next_attempt_at`, `priority` DESC) : sert au dispatcher (index partiel `WHERE status='queued'` sous PostgreSQL) ;
- (`conversation_id`, `created_at`) ;
- (`campaign_id`, `status`) ;
- (`phone`), (`created_at`), (`device_id`, `status`) ;
- index plein texte (`to_tsvector('simple', body)`) sous PostgreSQL pour la recherche dans la boîte de réception.

**message_events** : `id`, `message_id` FK (CASCADE), `status`, `error_code`, `details` JSON, `created_at`. Historique complet, affiché dans le détail d'un message.

**conversations** : `id`, `phone` E.164 UNIQUE, `contact_id` FK (null), `last_message_at`, `last_message_preview` varchar(160), `last_direction`, `unread_count`, `is_archived`, `assigned_to` FK→users (null).
**labels** : `id`, `name` UNIQUE, `color`.
**conversation_labels** : `conversation_id`, `label_id`, PK composite.

### 3.6 Automatisations
**schedules** (envois planifiés et récurrents)
| Colonne | Type | Notes |
|---|---|---|
| id | bigint | PK |
| uuid | uuid | |
| name | varchar(150) | |
| body / template_id | text / FK | |
| audience | JSON | même format que `campaigns.audience` |
| device_id, sim_slot | | |
| recurrence | varchar(10) | `once` \| `daily` \| `weekly` \| `monthly` |
| run_time | time | heure locale Casablanca |
| run_date | date | si `once` |
| days_of_week | JSON | `[1,3,5]` si `weekly` |
| day_of_month | smallint | si `monthly` (31 → dernier jour du mois) |
| start_date, end_date | date | |
| next_run_at | timestamptz | calculé, indexé |
| last_run_at | timestamptz | |
| last_campaign_id | FK→campaigns | |
| is_active | bool | |
| created_by, created_at | | |

Chaque exécution crée une **campagne** (`source='schedule'`), ce qui donne historique, pause et rapports gratuitement.

**automations** (déclencheurs)
| Colonne | Type | Notes |
|---|---|---|
| id | bigint | PK |
| name | varchar(150) | |
| trigger | varchar(20) | `due_reminder` \| `unpaid_followup` \| `birthday` \| `welcome` |
| config | JSON | `due_reminder` : `{"days_before":[3,1]}` ; `unpaid_followup` : `{"days_after":[1,7,15],"max":3}` ; `welcome` : `{"group_id":2}` |
| template_fr_id, template_ar_id | FK→templates | choisis selon `contacts.locale` |
| audience | JSON | filtre optionnel (groupes) |
| run_time | time | heure d'envoi quotidienne |
| is_active | bool | |
| last_run_at | timestamptz | |
| created_by, created_at | | |

**automation_runs** : `id`, `automation_id` FK, `contact_id` FK, `dedupe_key` varchar(120) (ex. `due:REF123:J-3`), `message_id` FK, `created_at`. Contrainte **UNIQUE (`automation_id`, `dedupe_key`)** : un même rappel n'est jamais envoyé deux fois.

**keyword_rules** (réponses automatiques)
| Colonne | Type | Notes |
|---|---|---|
| id | bigint | PK |
| keywords | JSON | `["INFO","معلومات"]` |
| match_type | varchar(12) | `exact` \| `starts_with` \| `contains` \| `regex` |
| action | varchar(12) | `reply` \| `opt_out` \| `opt_in` \| `label` \| `add_group` |
| reply_body | text | avec variables du contact |
| action_value | varchar(100) | label ou groupe cible |
| priority | int | |
| cooldown_minutes | int | anti-boucle : pas plus d'une réponse par numéro pendant N min |
| is_active | bool | |
| hit_count | int | |

### 3.7 Webhooks
**webhooks** : `id`, `uuid`, `url` (HTTPS obligatoire en prod), `events` JSON (`sms.sent`, `sms.delivered`, `sms.failed`, `sms.received`, `device.offline`, `device.online`), `secret_encrypted` (Fernet, nécessaire pour signer), `is_active`, `failure_count` (désactivation auto après 50 échecs consécutifs), `created_by`, `created_at`.

**webhook_deliveries** : `id`, `webhook_id` FK (CASCADE), `event`, `event_id` uuid (dédoublonnage côté client), `payload` JSON, `status` (`pending`\|`success`\|`failed`), `attempts`, `next_attempt_at`, `response_code`, `response_body` (tronqué à 1 Ko), `duration_ms`, `created_at`. Backoff : 30 s, 2 min, 10 min, 1 h, 6 h. Signature : `X-Signature: sha256=HMAC(secret, timestamp + "." + body)` et `X-Timestamp` (anti-rejeu, tolérance de 5 min).

### 3.8 Relations (résumé)
```
users 1─N campaigns, templates, imports, api_keys, audit_logs
devices 1─N device_sims ; devices 1─N messages
contacts N─N groups ; contacts 1─N receivables ; contacts 1─1 conversations
conversations 1─N messages ; conversations N─N labels
campaigns 1─N messages ; imports 1─N import_rows ; imports 1─1 campaigns
schedules 1─N campaigns ; automations 1─N automation_runs ─1 messages
messages 1─N message_events ; webhooks 1─N webhook_deliveries
```

---

## 4. Structure du projet

```
sms/
├── backend/
│   ├── app/
│   │   ├── __init__.py            # create_app(), enregistrement blueprints, scheduler
│   │   ├── config.py              # Dev / Prod / Test (variables d'environnement)
│   │   ├── extensions.py          # db, migrate, login, csrf, babel, socketio, limiter, smorest
│   │   ├── models/                # un fichier par domaine (user, device, contact, message…)
│   │   ├── services/
│   │   │   ├── phone.py           # normalisation E.164 Maroc
│   │   │   ├── sms_encoding.py    # GSM-7 / UCS-2, comptage de segments
│   │   │   ├── templating.py      # fusion {variables}, aperçu, variables manquantes
│   │   │   ├── importer.py        # lecture xlsx/csv, détection, mappage, validation
│   │   │   ├── queue.py           # mise en file, quotas, liste noire, fenêtre horaire
│   │   │   ├── dispatcher.py      # choix de l'appareil, push, timeouts, retry
│   │   │   ├── devices.py         # appairage, tokens, heartbeat
│   │   │   ├── inbound.py         # STOP, mots-clés, conversations
│   │   │   ├── automations.py     # échéances, impayés, anniversaires, bienvenue
│   │   │   ├── schedules.py       # calcul next_run_at
│   │   │   ├── webhooks.py        # signature HMAC, livraisons, retry
│   │   │   ├── reports.py         # stats, export Excel/PDF
│   │   │   └── security.py        # hash tokens, Fernet, audit
│   │   ├── web/                   # blueprint dashboard (routes Jinja + forms WTF)
│   │   ├── api/v1/                # blueprint flask-smorest (schémas marshmallow)
│   │   ├── device_api/            # endpoints HTTP du téléphone (repli)
│   │   ├── sockets/               # handlers Socket.IO /device et /dashboard
│   │   ├── tasks.py               # jobs APScheduler
│   │   ├── templates/             # Jinja (layout, partials, pages)
│   │   ├── static/                # css (thème, rtl), js (dashboard, socket, charts)
│   │   └── translations/          # fr, ar (.po/.mo)
│   ├── migrations/                # Alembic
│   ├── tests/                     # pytest : phone, encoding, templating, importer, queue, api
│   ├── manage.py                  # CLI : create-admin, seed, rotate-keys
│   ├── requirements.txt
│   └── Dockerfile
├── android/
│   ├── app/src/main/
│   │   ├── AndroidManifest.xml
│   │   ├── java/ma/smsgateway/
│   │   │   ├── App.kt
│   │   │   ├── data/              # Room : OutgoingSms, IncomingSms, StatusOutbox, DAO, DB
│   │   │   ├── net/               # ApiClient (OkHttp), SocketClient, DTO
│   │   │   ├── service/           # GatewayService, SmsSender, RateLimiter, SimManager
│   │   │   ├── receiver/          # SmsReceiver, BootReceiver, SentReceiver, DeliveredReceiver
│   │   │   ├── work/              # SyncWorker, WatchdogWorker (WorkManager)
│   │   │   ├── ui/                # MainActivity, ScanActivity, SettingsActivity, LogAdapter
│   │   │   └── util/              # Prefs (chiffrées), Permissions, BatteryOptim
│   │   └── res/                   # layouts, values (fr), values-ar
│   ├── build.gradle.kts, settings.gradle.kts, gradle.properties
├── deploy/
│   ├── docker-compose.yml         # web, db, nginx, certbot, backup
│   ├── nginx/sms.conf
│   ├── backup/backup.sh
│   └── .env.example
├── docs/
│   ├── 01-architecture-et-schema.md   (ce fichier)
│   ├── deploiement-vps.md
│   └── api.md                     # + Swagger UI sur /api/docs
└── samples/
    └── import_exemple.xlsx
```

---

## 5. Sécurité et conformité (résumé des mesures)
- **Transport** : HTTPS obligatoire (HSTS), WebSocket en `wss` uniquement, cookies `Secure`, `HttpOnly`, `SameSite=Lax`.
- **Secrets** : mots de passe hachés en argon2id ; tokens d'appareil et clés API stockés en SHA-256 (secrets à haute entropie, affichés une seule fois) ; secrets de webhook chiffrés en Fernet (clé `APP_ENCRYPTION_KEY`, rotation via `manage.py rotate-keys`). Les clés API ont une date d'expiration et une révocation.
- **Web** : CSRF (Flask-WTF), CSP stricte, échappement Jinja, requêtes ORM paramétrées, upload limité (10 Mo, extensions xlsx/csv vérifiées, stockage hors webroot).
- **Anti-abus** : rate limiting par clé et par IP (Flask-Limiter), quotas journaliers par clé, plafonds par SIM, seuil d'envoi massif suspect (pause automatique et alerte admin), verrouillage de compte après des échecs de connexion.
- **Loi 09-08** : statut et preuve du consentement par contact, STOP automatique et liste noire vérifiée à chaque envoi, mention STOP optionnelle par campagne, export ou suppression des données d'un contact (droit d'accès et de suppression), purge des fichiers d'import après 30 j, journal d'audit. *La déclaration ou l'autorisation auprès de la CNDP reste à votre charge : la plateforme fournit les outils, pas la démarche administrative.*
- **Sauvegardes** : `pg_dump` quotidien compressé, rotation sur 14 jours, script de restauration documenté.

---

## 6. Points à valider
1. **Table `messages` unique** (sortants et entrants) avec `direction`, plutôt que deux tables séparées.
2. **Table `receivables`** pour les échéances et impayés (importables depuis Excel), qui alimente `{montant}`, `{date_echeance}`, `{reference}` et les relances automatiques.
3. **Android 8 minimum** (minSdk 26).
4. **Fenêtre horaire par défaut** 08:00–21:00, 7 j/7, contournable seulement par les clés API autorisées (SMS transactionnels).
5. **Limites par défaut d'un appareil** : 1 SMS toutes les 5 s, 100/heure, 500/jour.
6. Nom du package Android : `ma.smsgateway` (modifiable).
