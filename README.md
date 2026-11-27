# Moodle Quiz Solver

Uniwersalny asystent AI do rozwiązywania quizów na platformie Moodle (dowolny przedmiot). Skrypt korzysta z biblioteki **Playwright** do obsługi przeglądarki, a jako silnik AI domyślnie wykorzystuje **Antigravity CLI (`agy`)** na Twojej subskrypcji (z opcjonalnym fallbackiem na Google AI Studio API).

## Cechy projektu
- **Uniwersalne profile przedmiotowe (`profiles.json`):** Wybór i przełączanie przedmiotu na żywo (np. Ogólny, Probabilistyka i Statystyka, Matematyka, Sieci Komputerowe / IT) z dedykowanym promptem systemowym i modelem, a także edycja/tworzenie profili bezpośrednio z CLI.
- **Podpinanie materiałów źródłowych (PDF / MD / TXT):** Możesz wskazać wykłady lub notatki z dysku (np. z sejfu Obsidian), na których AI opiera odpowiedzi w pierwszej kolejności.
- **Subskrypcja zamiast osobnego API (`agy CLI`):** Domyślnie używa `agy -p` (domyślny model `gemini-4-hydrogen-medium`) w trybie ustrukturyzowanego JSON-a i utrzymuje kontekst w ramach jednego quizu (`--conversation`), z opcją przełączenia na Google AI Studio API (`api`) bez wychodzenia z programu.
- **Interaktywna konsola `rich`:** Czytelny panel statusu sesji, tryb krokowy (`s`/`step`), opcje sesji (`confirm_before_next`, `show_reasoning`) oraz odporne zarządzanie przeglądarką (`url`, `open`) w jednej sesji logowania Moodle.
- **Pełna obsługa typów pytań Moodle:**
  - Jednokrotnego wyboru (`radio`) i wielokrotnego wyboru (`checkbox`) z bezpiecznym klikaniem przy wzorach MathJax
  - Pytania otwarte (`text` / `textarea`)
  - Dopasowanie (`select` - wiele list rozwijanych + analiza obrazków w wierszach)
- **Zawsze pod Twoją kontrolą:** W każdej chwili możesz wcisnąć **ESC** (lub **Ctrl+C**), żeby natychmiast przerwać tryb `AUTO`. Dodatkowo bot wypisuje krótkie uzasadnienie każdej decyzji AI (z możliwością wyłączenia w opcjach).
- **Wieloplatformowość bez uprawnień roota:** Przechwytywanie klawisza **ESC** działa natywnie bez biblioteki `keyboard` i bez `sudo` (Windows: `msvcrt`, Linux/macOS: `termios` + `select` w terminalu TTY, z fallbackiem na `SIGINT`). Obrazki z pytań są przetwarzane wyłącznie w pamięci (bez katalogu `.tmp_images`).

---

## 1. Instalacja (pierwsze uruchomienie)

Sklonuj repozytorium i uruchom skrypt instalacyjny, który utworzy `venv`, zainstaluje paczki oraz przeglądarkę Chromium dla Playwrighta:

- **Windows (CMD / PowerShell):**
  ```powershell
  git clone https://github.com/Dawe1600/Moodle_Quiz_Solver.git
  cd Moodle_Quiz_Solver
  .\setup.bat
  ```
- **Linux / macOS:**
  ```bash
  git clone https://github.com/Dawe1600/Moodle_Quiz_Solver.git
  cd Moodle_Quiz_Solver
  chmod +x setup.sh
  ./setup.sh
  ```

---

## 2. Jak przygotować profile (`profiles.json`)

Plik `profiles.json` jest ignorowany przez Gita (`.gitignore`), żeby Twoje lokalne ścieżki nie trafiały do repozytorium. Jeśli go nie utworzysz, bot uruchomi się z jednym domyślnym profilem `Ogólny` (a nowy plik możesz zapisać także bezpośrednio z menu `o` / `options`).

1. Skopiuj `profiles.example.json` i nazwij kopię `profiles.json`:
   ```powershell
   copy profiles.example.json profiles.json
   ```
2. Otwórz `profiles.json` i dodaj własne przedmioty według schematu:
   ```json
   {
     "probabilistyka": {
       "name": "Probabilistyka i Statystyka",
       "model": "gemini-4-hydrogen-medium",
       "fallback_models": [
         "gemma-4-31b-it",
         "gemma-4-26b-a4b-it"
       ],
       "system_prompt": "Jesteś ekspertem z rachunku prawdopodobieństwa i statystyki. Opieraj się przede wszystkim na podpiętych materiałach źródłowych.",
       "materials_paths": [
         "C:\\Sciezka\\Do\\Materialow\\wyklad.pdf"
       ]
     }
   }
   ```

### Opis pól w profilu
- **`name`**: Nazwa wyświetlana w menu startowym bota.
- **`model`**: Nazwa modelu używana przez `agy CLI` (listę dostępnych modeli sprawdzisz w terminalu poleceniem `agy models`).
- **`fallback_models`**: Lista modeli Google AI Studio używana tylko wtedy, gdy wybierzesz tryb API (`GEMINI_API_KEY`).
- **`system_prompt`**: Instrukcja roli i stylu rozwiązywania zadań dla danego przedmiotu.
- **`materials_paths`**: Lista absolutnych ścieżek do plików `.pdf`, `.md` lub `.txt` z wykładami i notatkami.
  - **Uwaga na Windowsie:** w formacie JSON każdy ukośnik w ścieżce musi być podwójny (`\\`), np. `C:\\Users\\PC\\Documents\\notatki.pdf`.
  - Jeśli ścieżka nie istnieje na dysku, bot ją bezpiecznie pominie.

---

## 3. Konfiguracja silnika AI

- **Opcja A: `agy CLI` (domyślna, z subskrypcji):** Jeśli masz zainstalowane i zalogowane `agy` w systemowym `PATH`, silnik zostanie wybrany automatycznie przy starcie bez dodatkowych pytań.
- **Opcja B: Google AI Studio API (opcjonalny fallback):** Skopiuj `.env.example` do `.env` i wpisz swój klucz `GEMINI_API_KEY`. Jeśli `agy` nie jest dostępne w `PATH` (lub wymusisz `--backend api`), bot automatycznie użyje API.

---

## 4. Jak odpalić i obsługiwać bota

> **Pełnoekranowy panel (Textual):** w interaktywnym terminalu (TTY) bot uruchamia panel z paskiem stanu sesji po lewej i strumieniem aktywności po prawej (`h` – pomoc, `q` – wyjście). Poza TTY (np. przekierowane wejście/wyjście, skrypty) działa dotychczasowy tekstowy interfejs opisany poniżej.

1. Aktywuj środowisko wirtualne i uruchom bota:
   - **Windows:**
     ```powershell
     .\venv\Scripts\activate
     python bot.py
     ```
   - **Linux / macOS:**
     ```bash
     source venv/bin/activate
     python bot.py
     ```
2. Bot automatycznie wybierze domyślny silnik AI (`agy` przed `api`) oraz pierwszy profil z `profiles.json` i od razu uruchomi przeglądarkę Chromium, wyświetlając w terminalu panel statusu (`rich`).
3. W otwartym oknie Chromium zaloguj się na Moodle i przejdź na pierwszą stronę wybranego quizu.
4. Sterowanie w terminalu:
   - **`Enter`**: Włącza tryb `AUTO` (bot rozwiązuje pytania na stronie i sam klika `Następna strona` aż do ekranu `Zapisz podejście`).
   - **`s`** / **`step`**: Rozwiązuje tylko bieżącą stronę quizu bez automatycznego przejścia dalej.
   - **`p`** / **`profile`**: Przełącza aktywny profil przedmiotu na żywo (resetując kontekst AI i ładując materiały nowego profilu bez zamykania przeglądarki).
   - **`b`** / **`backend`**: Przełącza silnik AI (`agy` ↔ `api`) na żywo bez utraty sesji przeglądarki.
   - **`m`** / **`model`**: Wybiera lub pozwala wpisać własny model AI dla aktywnego silnika (przy `agy` ukrywa modele dostępne wyłącznie w trybie `api`).
   - **`l`** / **`reload`**: Przeładowuje `profiles.json` z dysku bez zamykania przeglądarki.
   - **`o`** / **`options`**: Menu opcji sesji (`confirm_before_next` — pytanie przed przejściem do kolejnej strony, `show_reasoning` — wyświetlanie uzasadnień AI) oraz edycja/tworzenie profili z atomowym zapisem do `profiles.json`.
   - **`url [adres]`**: Przechodzi w przeglądarce pod nowy adres URL (lub otwiera przeglądarkę pod tym adresem).
   - **`open`**: Ponownie otwiera zamkniętą przeglądarkę pod bieżącym adresem.
   - **`ESC`**: Natychmiast przerywa tryb `AUTO` (działa również w trakcie oczekiwania na odpowiedź modelu).
   - **`r`** / **`reset`**: Resetuje pamięć rozmowy (`conversation_id` oraz cache materiałów) przed rozpoczęciem kolejnego quizu.
   - **`h`** / **`help`**: Wyświetla tabelę dostępnych komend.
   - **`q`** / **`quit`**: Zamyka przeglądarkę i kończy program.

### Opcjonalne flagi CLI

Możesz nadpisać domyślny profil, silnik lub adres logowania Moodle przy starcie:

```powershell
python bot.py --profile matematyka --backend agy --url https://moodle2.e-wsb.pl/login/index.php
```

- `--profile <klucz>`: Klucz profilu z `profiles.json` (np. `ogolny`).
- `--backend <agy|api>`: Wymuszenie silnika AI (`agy` lub `api`).
- `--url <adres>`: Adres startowy Moodle (można też ustawić zmienną `MOODLE_URL` w `.env`).

---

## 5. Testy i weryfikacja (`pytest` / `mypy`)

Zależności deweloperskie (`pytest`, `mypy`) są zdefiniowane w `pyproject.toml`:

```powershell
pip install -e ".[dev]"
python -m pytest
python -m mypy --strict
```
