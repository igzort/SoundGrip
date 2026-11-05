[app]

# (str) Title of the application
title = SoundGrip

# (str) Package name
package.name = soundgrip

# (str) Package domain (needed for android/ios packaging)
package.domain = org.soundgrip.game

# (str) Version of the application
version=0.1

# (str) Version matching your app, alternatives:
# version.regex = __version__ = ['\"](.*)['\"]
# version.filename = %(source.dir)s/main.py

# (str) Source code where the main.py lives
source.dir = .

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,kv,atlas,mp3,wav,glsl,vert,frag

# (list) Requirements
requirements = python3,kivy==2.3.0,pygame,pillow,kivy_deps.sdl2

# (str) Supported orientation (one of landscape, portrait, sensor, ...)
orientation = landscape

# (bool) Indicate if the application should be fullscreen or not
fullscreen = 1

# (list) Android permissions
android.permissions = VIBRATE, INTERNET

# (list) Targeted Android architectures
android.archs = armeabi-v7a, arm64-v8a

# (int) Android API level to target
android.api=34

# (int) Minimum Android API level supported
android.minapi=21

# (str) Android SDK version to use
android.sdk=34

# (str) Android Build-Tools version to use
android.build_tools_version=34.0.0

# (int) Android NDK API level to target
android.ndk_api=21

# (str) Android NDK version to use
# android.ndk = 25b

# (bool) Copy libs instead of creating a libpymodules.so
# android.copy_libs = 1

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug)
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_root = 1

# (str) Path to buildozer working directory
# build_dir = .buildozer
