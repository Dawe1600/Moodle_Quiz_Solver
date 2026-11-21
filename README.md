# Moodle Quiz Solver

Uniwersalny asystent AI do rozwiązywania quizów na platformie Moodle (dowolny przedmiot). Skrypt korzysta z biblioteki **Playwright** do obsługi przeglądarki, a jako silnik AI domyślnie wykorzystuje **Antigravity CLI (`agy`)** na Twojej subskrypcji (z opcjonalnym fallbackiem na Google AI Studio API).

## Cechy projektu
- **Uniwersalne profile przedmiotowe (`profiles.json`):** Wybór przedmiotu przy starcie (np. Ogólny, Probabilistyka i Statystyka, Matematyka, Sieci Komputerowe / IT) z dedykowanym promptem systemowym i modelem.
- **Podpinanie materiałów źródłowych (PDF / MD):** Możesz wskazać wykłady lub notatki z dysku (np. z sejfu Obsidian), na których AI opiera odpowiedzi w pierwszej kolejności.
- **Subskrypcja zamiast osobnego API (`agy CLI`):** Domyślnie używa `agy -p` w trybie ustrukturyzowanego JSON-a i utrzymuje kontekst w ramach jednego quizu (`--conversation`).
- **Pełna obsługa typów pytań Moodle:**
  - Jednokrotnego wyboru (`radio`)
  - Wielokrotnego wyboru (`checkbox`)
  - Pytania otwarte (`text` / `textarea`)
  - Dopasowanie (`select` - wiele list rozwijanych + analiza obrazków w wierszach)
- **Zawsze pod Twoją kontrolą:** W każdej chwili możesz wcisnąć **ESC**, żeby natychmiast przerwać tryb `AUTO`. Dodatkowo bot wypisuje krótkie uzasadnienie każdej decyzji AI.

## Szybka instalacja

- **Windows:**
  ```cmd
  .\setup.bat
  ```
- **Linux/macOS:**
  ```bash
  chmod +x setup.sh
  ./setup.sh
  ```

## Konfiguracja

1. **Silnik domyślny (`agy CLI`):** Jeśli masz zainstalowane i zalogowane `agy` w systemowym `PATH`, bot wykryje je automatycznie - nie potrzebujesz klucza API.
2. **Opcjonalny fallback (Google AI Studio API):** Skopiuj `.env.example` do `.env` i uzupełnij `GEMINI_API_KEY`.
3. **Profile i materiały (`profiles.json`):** Skopiuj `profiles.example.json` do `profiles.json` i dodaj lub edytuj ścieżki do własnych plików `.pdf` / `.md` w polu `materials_paths`.

## Użycie Bota

1. Aktywuj środowisko wirtualne: `.\venv\Scripts\activate`
2. Uruchom: `python bot.py`
3. Wybierz silnik AI i profil przedmiotu.
4. Zaloguj się na Moodle w otwartym oknie Chromium i wejdź do quizu.
5. Wciśnij `Enter` w terminalu, aby włączyć tryb `AUTO` (albo `ESC`, żeby zatrzymać; `r` resetuje kontekst AI przed nowym quizem).
