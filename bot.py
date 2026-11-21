import os
import sys
import json
import time
import shutil
import base64
import subprocess
from pathlib import Path
import keyboard
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

BASE_DIR = Path(__file__).resolve().parent
PROFILES_PATH = BASE_DIR / "profiles.json"
TMP_IMG_DIR = BASE_DIR / ".tmp_images"

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer_type": {
            "type": "string",
            "enum": ["radio", "checkbox", "text", "select"],
            "description": "Typ odpowiedzi na Moodle."
        },
        "selected_indices": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Indeksy poprawnych opcji (np. [0] lub [1, 2]) dla radio/checkbox."
        },
        "selected_options": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Lista dokładnych tekstów opcji dla pytań typu select."
        },
        "text_answer": {
            "type": "string",
            "description": "Dokładna odpowiedź do wpisania w polu tekstowym."
        },
        "reasoning": {
            "type": "string",
            "description": "Krótkie (1-2 zdania) uzasadnienie wyboru odpowiedzi."
        }
    },
    "required": ["answer_type", "reasoning"]
}


def check_escape(raise_exc=False):
    escaped = False
    try:
        if keyboard.is_pressed("esc"):
            escaped = True
    except Exception:
        pass
    if escaped and raise_exc:
        raise InterruptedError("Przerwano przez klawisz ESC")
    return escaped


def load_profiles():
    if not PROFILES_PATH.exists():
        return {
            "ogolny": {
                "name": "Ogólny",
                "model": "gemini-3.8-flash-medium",
                "fallback_models": ["gemma-4-31b-it", "gemma-4-26b-a4b-it", "gemini-3-flash-preview"],
                "system_prompt": "Jesteś ekspertem akademickim rozwiązującym quizy na Moodle.",
                "materials_paths": []
            }
        }
    with open(PROFILES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def select_profile(profiles):
    keys = list(profiles.keys())
    print("\n=== Wybierz profil przedmiotu ===")
    for idx, key in enumerate(keys, 1):
        prof = profiles[key]
        mats = [Path(m).name for m in prof.get("materials_paths", []) if Path(m).exists()]
        mats_info = f" | Materiały: {', '.join(mats)}" if mats else ""
        print(f"  [{idx}] {prof.get('name', key)} (model: {prof.get('model')}){mats_info}")

    while True:
        choice = input(f"\nWybierz numer [1-{len(keys)}] (Enter = 1): ").strip()
        if not choice:
            return keys[0], profiles[keys[0]]
        if choice.isdigit() and 1 <= int(choice) <= len(keys):
            k = keys[int(choice) - 1]
            return k, profiles[k]
        print("Nieprawidłowy wybór, spróbuj ponownie.")


def select_backend():
    agy_bin = shutil.which("agy")
    has_api = bool(GEMINI_API_KEY and "twoj_klucz" not in GEMINI_API_KEY and HAS_GENAI)

    if agy_bin and has_api:
        print("\n=== Wybierz silnik AI ===")
        print("  [1] agy CLI (subskrypcja Antigravity - domyślny)")
        print("  [2] Google AI Studio API (GEMINI_API_KEY z .env)")
        choice = input("Wybór [1/2] (Enter = 1): ").strip()
        if choice == "2":
            return "api", None
        return "agy", agy_bin
    elif agy_bin:
        print(f"\nSilnik AI: agy CLI ({agy_bin})")
        return "agy", agy_bin
    elif has_api:
        print("\nSilnik AI: Google AI Studio API (fallback, nie znaleziono agy w PATH)")
        return "api", None
    else:
        return None, None


def save_temp_image(page, img_element, name_prefix):
    try:
        TMP_IMG_DIR.mkdir(parents=True, exist_ok=True)
        img_element.scroll_into_view_if_needed()
        img_bytes = img_element.screenshot()
        img_path = TMP_IMG_DIR / f"{name_prefix}_{int(time.time() * 1000)}.png"
        with open(img_path, "wb") as f:
            f.write(img_bytes)
        return img_path, base64.b64encode(img_bytes).decode("utf-8")
    except Exception as e:
        print(f"Błąd podczas pobierania obrazka: {e}")
        return None, None


def cleanup_temp_images():
    if TMP_IMG_DIR.exists():
        for p in TMP_IMG_DIR.glob("*.png"):
            try:
                p.unlink()
            except Exception:
                pass


def call_agy(prompt, profile, agy_bin, conversation_id=None, image_paths=None):
    schema_str = json.dumps(ANSWER_SCHEMA, ensure_ascii=False)
    model = profile.get("model", "gemini-3.8-flash-medium")
    existing_materials = [m for m in profile.get("materials_paths", []) if Path(m).exists()]

    cmd = [
        agy_bin,
        "--model", model,
        "--output-format", "json",
        "--json-schema", schema_str,
        "--disable-slash-commands",
        "--dangerously-skip-permissions",
        "--add-dir", str(BASE_DIR)
    ]

    add_dirs = set()
    for m in existing_materials:
        add_dirs.add(str(Path(m).parent))
    for d in add_dirs:
        cmd.extend(["--add-dir", d])

    if conversation_id:
        cmd.extend(["--conversation", conversation_id])

    parts = []
    if not conversation_id:
        parts.append(f"INSTRUKCJA SYSTEMOWA:\n{profile.get('system_prompt', '')}")
        if existing_materials:
            mat_list = "\n".join(f"- {m}" for m in existing_materials)
            parts.append(
                "GŁÓWNE MATERIAŁY ŹRÓDŁOWE (przeczytaj / przeszukaj je narzędziami przed udzieleniem odpowiedzi, "
                "aby zachować pełną zgodność z wykładami):\n" + mat_list
            )
    if image_paths:
        img_list = "\n".join(f"- {p}" for p in image_paths)
        parts.append(
            "OBRAZKI Z PYTANIA (odczytaj je dokładnie z dysku, zwracając uwagę na wykresy, wzory i liczby):\n" + img_list
        )

    parts.append(
        "PYTANIE Z MOODLE:\n" + prompt +
        "\n\nZwróć wynik wyłącznie w zadanym schemacie JSON."
    )
    full_prompt = "\n\n".join(parts)
    cmd.extend(["-p", full_prompt])

    print(f"Wysyłam zapytanie do agy CLI (model: {model})...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8"
    )

    while proc.poll() is None:
        if check_escape(False):
            proc.terminate()
            raise InterruptedError("Przerwano przez klawisz ESC")
        time.sleep(0.15)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        print(f"Błąd agy CLI (kod {proc.returncode}): {stderr.strip()}")
        return None, conversation_id

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as e:
        print(f"Nie udało się sparsować odpowiedzi z agy CLI: {e}\nOutput: {stdout[:500]}")
        return None, conversation_id

    new_conv_id = envelope.get("conversation_id") or conversation_id
    ans = envelope.get("structured_output")
    if isinstance(ans, str):
        try:
            ans = json.loads(ans)
        except Exception:
            ans = None

    if not ans and envelope.get("response"):
        raw = envelope["response"].strip()
        if "```json" in raw:
            raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
        try:
            ans = json.loads(raw)
        except Exception:
            pass

    return ans, new_conv_id


def call_gemini_api_vision(b64_image):
    client = genai.Client(api_key=GEMINI_API_KEY)
    for model in ["gemini-2.5-flash", "gemini-2.5-flash-lite"]:
        check_escape(True)
        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(
                        data=base64.b64decode(b64_image),
                        mime_type="image/png",
                    ),
                    "Opisz dokładnie ten obrazek na potrzeby rozwiązania pytania testowego. Zwróć uwagę na wzory, liczby, wykresy i tekst."
                ]
            )
            return response.text
        except Exception as e:
            print(f"Wyjątek Vision ({model}): {e}")
    return ""


def call_gemini_api(prompt, profile, image_b64_list=None):
    client = genai.Client(api_key=GEMINI_API_KEY)
    contents = [profile.get("system_prompt", "")]

    for m in profile.get("materials_paths", []):
        p = Path(m)
        if not p.exists():
            continue
        if p.suffix.lower() == ".pdf":
            with open(p, "rb") as f:
                contents.append(types.Part.from_bytes(data=f.read(), mime_type="application/pdf"))
        elif p.suffix.lower() in [".md", ".txt"]:
            with open(p, "r", encoding="utf-8") as f:
                contents.append(f"Materiał ({p.name}):\n{f.read()}")

    if image_b64_list:
        for b64 in image_b64_list:
            contents.append(types.Part.from_bytes(data=base64.b64decode(b64), mime_type="image/png"))

    full_prompt = (
        "MUSISZ zwrócić dane wyłącznie w formacie JSON zgodnym ze schematem:\n"
        + json.dumps(ANSWER_SCHEMA, ensure_ascii=False)
        + "\n\n"
        + prompt
    )
    contents.append(full_prompt)

    models_to_try = profile.get("fallback_models", ["gemma-4-31b-it", "gemma-4-26b-a4b-it", "gemini-2.5-flash"])
    for model in models_to_try:
        check_escape(True)
        try:
            print(f"Próbuję model API: {model}...")
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Wyjątek dla {model}: {e}")
    return None


def parse_question(page, q_container, backend):
    q_text_el = q_container.locator(".qtext")
    q_text = q_text_el.inner_text() if q_text_el.count() > 0 else ""

    image_paths = []
    image_b64_list = []
    img_notes = []

    images = q_text_el.locator("img")
    for j in range(images.count()):
        img_path, b64 = save_temp_image(page, images.nth(j), f"q_{j}")
        if img_path:
            image_paths.append(img_path)
            image_b64_list.append(b64)
            img_notes.append(f"[Obrazek w treści pytania #{j+1}: {img_path.name}]")

    prompt_options = ""
    labels_elements = []

    labels = q_container.locator(".answer [data-region='answer-label'], .answer label")
    if labels.count() > 0:
        for j in range(labels.count()):
            lbl = labels.nth(j)
            labels_elements.append(lbl)
            lbl_text = lbl.inner_text().strip()
            lbl_imgs = lbl.locator("img")
            opt_img_tags = []
            for k in range(lbl_imgs.count()):
                img_path, b64 = save_temp_image(page, lbl_imgs.nth(k), f"opt_{j}_{k}")
                if img_path:
                    image_paths.append(img_path)
                    image_b64_list.append(b64)
                    if backend == "api":
                        desc = call_gemini_api_vision(b64)
                        opt_img_tags.append(f"[Obrazek: {desc}]")
                    else:
                        opt_img_tags.append(f"[Obrazek opcji {j}: {img_path.name}]")
            combined = (lbl_text + " " + " ".join(opt_img_tags)).strip()
            prompt_options += f"Opcja {j}: {combined}\n"

    inputs = q_container.locator(
        "input:not([type='hidden']):not([type='radio']):not([type='checkbox']):not([type='submit']):not([type='button']), textarea"
    )
    has_text = inputs.count() > 0
    is_multiple = q_container.locator("input[type='checkbox']").count() > 0

    selects = q_container.locator("select")
    if selects.count() > 0:
        prompt_options += "\nTo jest pytanie typu DOPASOWANIE (listy rozwijane). Podaj poprawne opcje w 'selected_options' w kolejności:\n"
        for j in range(selects.count()):
            sel = selects.nth(j)
            row = sel.locator("xpath=./ancestor::tr").first
            context_text = f"Lista {j+1}"
            if row.count() > 0:
                text_td = row.locator("td.text")
                if text_td.count() == 0:
                    text_td = row
                td_str = text_td.inner_text().strip()
                imgs = text_td.locator("img")
                row_img_tags = []
                for k in range(imgs.count()):
                    img_path, b64 = save_temp_image(page, imgs.nth(k), f"sel_{j}_{k}")
                    if img_path:
                        image_paths.append(img_path)
                        image_b64_list.append(b64)
                        if backend == "api":
                            desc = call_gemini_api_vision(b64)
                            row_img_tags.append(f"[Obrazek: {desc}]")
                        else:
                            row_img_tags.append(f"[Obrazek wiersza {j+1}: {img_path.name}]")
                context_text = (td_str + " " + " ".join(row_img_tags)).strip()

            opts = sel.locator("option").all_inner_texts()
            opts = [o.strip() for o in opts if o.strip() and "Wybierz" not in o]
            prompt_options += f"Dla elementu '{context_text}' -> Wybierz jedną z opcji: {', '.join(opts)}\n"

    prompt = f"Treść:\n{q_text}\n"
    if img_notes:
        prompt += "\n".join(img_notes) + "\n"
    if prompt_options:
        prompt += (
            f"\nDostępne opcje:\n{prompt_options}\n"
            "Zwróć 'answer_type' ('radio', 'checkbox' lub 'select') oraz odpowiedni wybór."
        )
        if is_multiple:
            prompt += (
                "\nUWAGA: Pytanie WIELOKROTNEGO WYBORU (checkbox). Zwróć wszystkie poprawne indeksy w 'selected_indices' "
                "(może być 1 lub więcej)."
            )
    elif has_text:
        prompt += "\nPytanie otwarte. Zwróć 'answer_type': 'text' i wpisz wynik w 'text_answer'."

    choice_inputs = q_container.locator(".answer input[type='radio'], .answer input[type='checkbox']")

    return prompt, labels_elements, choice_inputs, selects, inputs, image_paths, image_b64_list


def apply_answer(ans, labels_elements, choice_inputs, selects, inputs):
    if not ans:
        return

    if ans.get("reasoning"):
        print(f"Uzasadnienie AI: {ans['reasoning']}")

    a_type = ans.get("answer_type")
    if a_type in ["radio", "checkbox"] and ans.get("selected_indices") is not None:
        for idx in ans["selected_indices"]:
            try:
                if 0 <= idx < choice_inputs.count():
                    choice_inputs.nth(idx).check(force=True)
                    print(f"Zaznaczono opcję {idx}")
                elif 0 <= idx < len(labels_elements):
                    labels_elements[idx].click(position={"x": 5, "y": 5})
                    print(f"Zaznaczono opcję {idx}")
                else:
                    print(f"Błąd: Nieprawidłowy indeks opcji: {idx}")
            except Exception as e:
                print(f"Błąd kliknięcia opcji {idx}: {e}")

    elif a_type == "select" and ans.get("selected_options"):
        for j in range(min(selects.count(), len(ans["selected_options"]))):
            opt = ans["selected_options"][j]
            try:
                selects.nth(j).select_option(label=opt)
                print(f"Wybrano z listy {j+1}: {opt}")
            except Exception as e:
                print(f"Nie udało się wybrać '{opt}' z listy {j+1}: {e}")

    elif a_type == "text" and ans.get("text_answer") is not None:
        if inputs.count() > 0:
            inputs.first.fill(str(ans["text_answer"]))
            print(f"Wpisano tekst: {ans['text_answer']}")
        else:
            print(f"UWAGA: Brak pola tekstowego na stronie dla odpowiedzi: {ans['text_answer']}")


def handle_navigation(page):
    next_btn = page.locator("input#mod_quiz-next-nav[value*='Następna']")
    end_btn = page.locator("input#mod_quiz-next-nav[value*='Zapisz podejście']")

    if next_btn.count() > 0:
        print("Przechodzę do następnej strony...")
        next_btn.click()
        time.sleep(1)
        return True
    elif end_btn.count() > 0:
        print("Quiz zakończony (pojawiło się 'Zapisz podejście'). Przechodzę w tryb oczekiwania.")
        return False
    else:
        print("Brak przycisku nawigacji. Zatrzymuję tryb AUTO.")
        return False


def run_bot():
    backend, agy_bin = select_backend()
    if not backend:
        print("BŁĄD: Nie znaleziono `agy` w PATH ani ważnego klucza `GEMINI_API_KEY` w pliku `.env`.")
        return

    profiles = load_profiles()
    _, profile = select_profile(profiles)
    print(f"\nWybrano profil: {profile['name']}")

    conversation_id = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        page.goto("https://moodle2.e-wsb.pl/login/index.php")
        print("Zaloguj się i wejdź do quizu.")

        auto_mode = False

        while True:
            if not auto_mode:
                cmd = input("\nWciśnij [Enter] aby włączyć tryb AUTO, 'r' aby zresetować kontekst rozmowy, lub 'q' aby wyjść: ")
                if cmd.lower() == "q":
                    break
                if cmd.lower() == "r":
                    conversation_id = None
                    print("Zresetowano sesję kontekstu AI.")
                    continue
                auto_mode = True

            try:
                check_escape(True)
                print("Analizuję stronę... (wciśnij ESC w terminalu, aby natychmiast przerwać!)")
                questions = page.locator(".que")
                count = questions.count()
                if count == 0:
                    print("Nie znaleziono pytania (.que). Czy jesteś w quizie?")
                    time.sleep(2)
                    continue

                all_answered = True
                for i in range(count):
                    q_container = questions.nth(i)
                    prompt, labels_elements, choice_inputs, selects, inputs, img_paths, img_b64 = parse_question(
                        page, q_container, backend
                    )

                    print(f"\n--- Pytanie {i+1}/{count} ---")
                    if backend == "agy":
                        ans, conversation_id = call_agy(
                            prompt, profile, agy_bin, conversation_id=conversation_id, image_paths=img_paths
                        )
                    else:
                        ans = call_gemini_api(prompt, profile, image_b64_list=img_b64)

                    cleanup_temp_images()
                    print(f"Odpowiedź JSON: {ans}")
                    if not ans:
                        print("UWAGA: Brak odpowiedzi AI. Zatrzymuję tryb AUTO i nie przechodzę dalej.")
                        all_answered = False
                        break
                    apply_answer(ans, labels_elements, choice_inputs, selects, inputs)

                check_escape(True)
                if not all_answered or not handle_navigation(page):
                    auto_mode = False

            except InterruptedError as e:
                cleanup_temp_images()
                print(f"\n[!] {e}. Zatrzymano tryb AUTO.")
                auto_mode = False
            except Exception as e:
                cleanup_temp_images()
                print(f"\nWystąpił nieoczekiwany błąd: {e}")
                auto_mode = False
                time.sleep(2)


if __name__ == "__main__":
    run_bot()
