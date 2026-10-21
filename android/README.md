# SMS Gateway : application Android

Transforme un téléphone Android (8.0 ou plus récent) en passerelle SMS pilotée par le serveur.

## Fonctionnement

| Élément | Rôle |
|---|---|
| `GatewayService` | Service de premier plan (type `remoteMessaging`, compatible Android 14/15) : WebSocket, heartbeat 30 s, polling de secours 15 s, envoi des SMS. Verrou CPU partiel pour fonctionner écran éteint. |
| `SocketClient` | Socket.IO, namespace `/device`, token dans `auth`. Reconnexion automatique (2 s → 30 s). |
| `ApiClient` | API HTTP `/device/v1/*` : appairage et repli quand le WebSocket est coupé. |
| `SmsSender` | `SmsManager` multipart (GSM-7 / UCS-2, donc arabe compris), choix de la SIM, un accusé d'envoi et de livraison par segment. |
| `SmsStatusReceiver` | Accusés : `sent` → `delivered` ou `failed` + code d'erreur (`RADIO_OFF`, `NO_SERVICE`…). |
| `SmsReceiver` | SMS entrants : segments réassemblés, stockés en Room puis transmis au serveur. |
| Room | File locale `outgoing`, boîte d'envoi des statuts, SMS reçus non synchronisés, journal. Tout est rejoué à la reconnexion (mode hors ligne). |
| `WatchdogWorker` | WorkManager toutes les 15 min : relance le service si le système l'a tué. |
| `BootReceiver` | Démarrage automatique après redémarrage ou mise à jour de l'APK. |

**Limiteur local** : délai entre SMS, maximum par heure et par jour (réglés depuis le tableau de bord et reçus via `config.update`), plus un seuil de batterie local (pas d'envoi sous X % sauf en charge).

## Compiler

Prérequis : JDK 17 (ou 21) et Android SDK 35 — ou Android Studio. Gradle 8.10 ne supporte pas Java 24+ : si `java -version` affiche une version plus récente, définissez `JAVA_HOME` vers un JDK 17.

```bash
./gradlew assembleDebug
```

APK obtenu : `app/build/outputs/apk/debug/app-debug.apk` (identifiant `ma.smsgateway.debug`, installable à côté de la version release).

### APK de production signé

1. Créer une clé, une seule fois, et la conserver précieusement : une mise à jour doit être signée avec la même clé.

```bash
keytool -genkeypair -v -keystore smsgateway.jks -alias smsgateway -keyalg RSA -keysize 4096 -validity 10000
```

2. Créer `android/keystore.properties` (non versionné) :

```properties
storeFile=smsgateway.jks
storePassword=…
keyAlias=smsgateway
keyPassword=…
```

3. Compiler :

```bash
./gradlew assembleRelease
```

L'APK signé est dans `app/build/outputs/apk/release/app-release.apk`.

## Installer sur le téléphone

L'app est distribuée en APK direct : Google Play interdit les permissions SMS hors application SMS par défaut.

1. Copier l'APK sur le téléphone (câble, lien de téléchargement depuis votre serveur, etc.) et l'ouvrir. Autoriser « Installer des applications inconnues » pour le gestionnaire de fichiers.
   Par câble : `adb install -r app-release.apk`
2. Ouvrir SMS Gateway et accorder les permissions SMS, téléphone et notifications.
   Sur Android 13 et plus, si les permissions SMS sont grisées : Paramètres › Applications › SMS Gateway › ⋮ › **Autoriser les paramètres restreints**, puis revenir dans l'app.
3. Toucher **Désactiver l'optimisation de batterie** et suivre le conseil propre au fabricant affiché (Xiaomi : démarrage automatique, etc.).
4. Dans le tableau de bord : **Appareils › Ajouter un appareil › Générer le QR code**, puis dans l'app **Scanner le QR code**.
   Le tableau de bord bascule automatiquement sur la fiche du téléphone dès l'appairage.

## Conseils d'exploitation

- Laisser le téléphone **en charge**, avec Wi-Fi ou 4G stable. La passerelle maintient le CPU actif.
- Garder des limites prudentes (par exemple 1 SMS toutes les 5 s, 100 par heure, 500 par jour) pour éviter le blocage de la SIM par l'opérateur.
- Désactiver les mises à jour automatiques du système et le redémarrage programmé.
- En cas de révocation ou de réappairage depuis le tableau de bord, l'app affiche une alerte. Il suffit alors de rescanner un QR.
- Développement en réseau local : le HTTP est accepté (avec un avertissement à l'écran). En production, utilisez HTTPS (étape de déploiement).

## Structure

```
app/src/main/java/ma/smsgateway/
├── App.kt                 Application : singletons, canaux de notification, watchdog
├── data/                  Prefs (chiffrées), entités et DAO Room, journal
├── net/                   ApiClient (OkHttp), SocketClient (Socket.IO)
├── sms/                   SimManager (double SIM), SmsSender, ErrorCodes
├── service/               GatewayService, GatewayRepository (file locale), GatewayState
├── receiver/              SmsReceiver, SmsStatusReceiver, BootReceiver
├── work/                  WatchdogWorker
└── ui/                    MainActivity, SettingsActivity, Pairing, Permissions, LogAdapter
```
