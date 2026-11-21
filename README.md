# Moodle Quiz Solver

Uniwersalny asystent AI do rozwiązywania quizów na platformie Moodle (dowolny przedmiot). Skrypt korzysta z biblioteki **Playwright** do obsługi przeglądarki, a jako silnik AI domyślnie wykorzystuje **Antigravity CLI (`agy`)** na Twojej subskrypcji (z opcjonalnym fallbackiem na Google AI Studio API).

## Cechy projektu
- **Uniwersalne profile przedmiotowe (`profiles.json`):** Wybór przedmiotu przy starcie (np. Ogólny, Probabilistyka i Statystyka, Matematyka, Sieci Komputerowe / IT) z dedykowanym promptem systemowym i modelem.
- **Podpinanie materiałów źródłowych (PDF / MD):** Możesz wskazać wykłady lub notatki z dysku (np. z sejfu Obsidian), na których AI opiera odpowiedzi w pierwszej kolejności.
- **Subskrypcja zamiast osobnego API (`agy CLI`):** Domyślnie używa `agy -p` w trybie ustrukturyzowanego JSON-a i utrzymuje kontekst w ramach jednego quizu (`--conversation`).
- **Pełna obsługa typów pytań Moodle:**
  - Jednokrotnego wyboru (`radio`) i wielokrotnego wyboru (`checkbox`) z bezpiecznym klikaniem przy wzorach MathJax
  - Pytania otwarte (`text` / `textarea`)
  - Dopasowanie (`select` - wiele list rozwijanych + analiza obrazków w wierszach)
- **Zawsze pod Twoją kontrolą:** W każdej chwili możesz wcisnąć **ESC**, żeby natychmiast przerwać tryb `AUTO`. Dodatkowo bot wypisuje krótkie uzasadnienie każdej decyzji AI.

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

Plik `profiles.json` jest ignorowany przez Gita (`.gitignore`), żeby Twoje lokalne ścieżki nie trafiały do repozytorium. Jeśli go nie utworzysz, bot uruchomi się z jednym domyślnym profilem `Ogólny`.

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

- **Opcja A: `agy CLI` (domyślna, z subskrypcji):** Jeśli masz zainstalowane i zalogowane `agy` w systemowym `PATH`, nic więcej nie musisz konfigurować.
- **Opcja B: Google AI Studio API (opcjonalny fallback):** Skopiuj `.env.example` do `.env` i wpisz swój klucz `GEMINI_API_KEY`. Jeśli masz oba, przy starcie wybierzesz silnik w menu (`[1]` agy CLI / `[2]` API).

---

## 4. Jak odpalić i obsługiwać bota

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
2. Wybierz silnik AI (jeśli masz oba) oraz numer profilu przedmiotu.
3. W otwartym oknie Chromium zaloguj się na Moodle i przejdź na pierwszą stronę wybranego quizu.
4. Sterowanie w terminalu:
   - **`Enter`**: Włącza tryb `AUTO` (bot rozwiązuje pytania na stronie i sam klika `Następna strona` aż do ekranu `Zapisz podejście`).
   - **`ESC`**: Natychmiast przerywa tryb `AUTO` (działa również w trakcie oczekiwania na odpowiedź modelu).
   - **`r`**: Resetuje pamięć rozmowy (`conversation_id`) przed rozpoczęciem kolejnego quizu.
   - **`q`**: Zamyka bota.
