    @echo off
REM ============================================================
REM  Avvio del tool analisi bollette (Django).
REM  - Porta di default: 8001 (override: avvia.bat <porta>)
REM  - Carica le variabili da .env se presente
REM  - Se la porta e' occupata, chiude il processo che la usa
REM  - Applica le migrazioni e avvia il server
REM ============================================================
setlocal enabledelayedexpansion

REM Posizionati nella cartella dello script (gestisce gli spazi nel path)
cd /d "%~dp0"

REM --- Porta (arg 1, default 8000) ---
set "PORTA=%~1"
if "%PORTA%"=="" set "PORTA=8001"

REM --- Scegli l'interprete Python (venv se presente, altrimenti di sistema) ---
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

REM --- Carica le variabili d'ambiente da .env (ignora commenti e righe vuote) ---
if exist ".env" (
    echo [env] Carico variabili da .env
    for /f "usebackq eol=# tokens=1,* delims==" %%k in (".env") do (
        if not "%%k"=="" set "%%k=%%l"
    )
) else (
    echo [env] Nessun file .env trovato: uso solo le variabili gia' presenti.
)

REM --- Controllo occupazione porta ---
echo [porta] Controllo chi usa la porta %PORTA% ...
set "PID_OCC="
for /f "tokens=5" %%p in ('netstat -aon ^| findstr /r /c:":%PORTA% .*LISTENING"') do (
    set "PID_OCC=%%p"
)

if defined PID_OCC (
    echo [porta] La porta %PORTA% e' occupata dal PID !PID_OCC! :
    for /f "tokens=1" %%n in ('tasklist /fi "PID eq !PID_OCC!" /fo csv /nh') do echo        processo %%n
    echo [porta] Chiudo il processo PID !PID_OCC! ...
    taskkill /F /PID !PID_OCC! >nul 2>&1
    if errorlevel 1 (
        echo [porta] ATTENZIONE: impossibile chiudere il PID !PID_OCC!. Procedo comunque.
    ) else (
        echo [porta] Processo chiuso.
    )
) else (
    echo [porta] Porta %PORTA% libera.
)

REM --- Migrazioni ---
echo [django] Applico le migrazioni ...
%PY% manage.py migrate
if errorlevel 1 (
    echo [django] ERRORE durante le migrazioni. Interrompo.
    goto :fine
)

REM --- Superuser: crea al primo avvio se non ne esiste nessuno ---
%PY% manage.py shell -c "import sys;from django.contrib.auth import get_user_model;sys.exit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 1)"
if errorlevel 1 (
    echo [django] Nessun superuser presente: lo creo ...
    if defined DJANGO_SUPERUSER_USERNAME (
        if defined DJANGO_SUPERUSER_PASSWORD (
            echo [django] Creazione automatica utente "!DJANGO_SUPERUSER_USERNAME!" da .env
            %PY% manage.py createsuperuser --noinput
        ) else (
            echo [django] DJANGO_SUPERUSER_PASSWORD vuota: inserisci le credenziali.
            %PY% manage.py createsuperuser
        )
    ) else (
        echo [django] Variabili DJANGO_SUPERUSER_* assenti: inserisci le credenziali.
        %PY% manage.py createsuperuser
    )
) else (
    echo [django] Superuser gia' presente.
)

REM --- Avvio server ---
echo [django] Avvio il server su http://127.0.0.1:%PORTA%/  (Ctrl+C per fermare)
%PY% manage.py runserver 127.0.0.1:%PORTA%

:fine
echo.
echo [fine] Server terminato.
endlocal
pause
