@echo off
REM Wrapper: system Python 3.12 has Kivy 2.3.1; the project venv (env\) does NOT.
REM Вывод -- в консоль (как обычно). Перенаправление в файл включайте вручную:
REM   _py.bat script.py > out.txt 2>&1
set "PY=C:\Users\dota-\AppData\Local\Programs\Python\Python312\python.exe"
cd /d "C:\python projects\SoundGrip"
"%PY%" %*
exit /b %ERRORLEVEL%
