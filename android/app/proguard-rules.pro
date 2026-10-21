# Socket.IO / Engine.IO (réflexion sur les transports)
-keep class io.socket.** { *; }
-dontwarn io.socket.**
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**

# Room : entités et DAO générés
-keep class ma.smsgateway.data.** { *; }

# ZXing
-keep class com.journeyapps.barcodescanner.** { *; }

# EncryptedSharedPreferences (Tink)
-dontwarn com.google.errorprone.annotations.**
-dontwarn javax.annotation.**
