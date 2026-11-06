[app]
title = SoundGrip
package.name = soundgrip
package.domain = org.igzort
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,mp3,wav
version = 0.1
requirements = python3,kivy==2.3.0,pillow
orientation = landscape
fullscreen = 1
android.permissions = VIBRATE, INTERNET
android.archs = armeabi-v7a, arm64-v8a
android.allow_backup = True

# ЖЕСТКИЕ ПУТИ К ГОТОВОМУ SDK И НАСТРОЙКАМ НА СЕРВЕРЕ GITHUB
android.sdk_path = /usr/local/lib/android/sdk
android.ndk_path = /usr/local/lib/android/sdk/ndk/25.2.9519653
android.api = 34
android.minapi = 21
android.ndk_api = 21

[buildozer]
log_level = 2
warn_on_root = 1
