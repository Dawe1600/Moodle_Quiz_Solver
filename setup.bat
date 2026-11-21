@echo off
echo ========================================================
echo Konfigurowanie srodowiska dla Moodle Quiz Solver...
echo ========================================================

echo.
echo Tworzenie wirtualnego srodowiska (venv)...
python -m venv venv

echo.
echo Aktywacja wirtualnego srodowiska i instalacja bibliotek...
call venv\Scripts\activate.bat
pip install -r requirements.txt

echo.
echo Instalowanie przegladarek dla biblioteki Playwright...
playwright install chromium

echo.
echo ========================================================
echo Konfiguracja zakonczona sukcesem!
echo ========================================================
echo Domyslny silnik: agy CLI (subskrypcja Antigravity).
echo Opcjonalnie (dla fallbacku API): skopiuj .env.example do .env i wpisz GEMINI_API_KEY.
echo.
echo Aby uruchomic bota, wpisz w konsoli:
echo call venv\Scripts\activate.bat
echo python bot.py
echo ========================================================
pause
