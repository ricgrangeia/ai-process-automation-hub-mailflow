# Localisation (i18n)

This folder contains all translatable strings for the project — LLM prompts and UI messages (Telegram buttons, messages, labels).

## How it works

The active language is selected via the `LANGUAGE` environment variable (default: `en`).  
If a string is missing in the selected language, it automatically falls back to `en`.

```
LANGUAGE=pt   # use Portuguese
LANGUAGE=en   # use English (default)
```

In code, strings are accessed via the `t()` helper:

```python
from app.core.i18n import t

# LLM prompt
prompt = t("prompt.classifier.system")

# UI string
header = t("telegram.review.header")

# UI string with variable substitution
btn    = t("telegram.buttons.approve", folder="Invoices")
msg    = t("telegram.review.ai_decision", folder="Invoices", confidence=92, source="llm")
```

---

## Folder structure

```
locales/
  en/                                      ← English (base, always the fallback)
    ui.toml                                ← all UI strings: buttons, messages, labels
    prompt.classifier.system.txt           ← LLM prompts (one file each)
    prompt.classifier.user.txt
    prompt.classifier.rule_hint.txt
    prompt.query.parser.txt

  pt/                                      ← Portuguese translation
    ui.toml
    prompt.*.txt                           ← same filenames, translated content
```

### Two file types

| Type | File | Used for | Format |
|------|------|----------|--------|
| LLM prompts | `prompt.*.txt` | Instructions sent to the AI model | Plain text, fully readable |
| UI strings | `ui.toml` | Telegram buttons, messages, labels | TOML — grouped sections |

---

## Naming rules

- **Prompts** — filename must start with `prompt.` followed by a dot-separated path:
  ```
  prompt.classifier.system.txt
  prompt.classifier.user.txt
  prompt.query.parser.txt
  ```

- **UI strings** — all in `ui.toml`, grouped by section:
  ```toml
  [telegram.buttons]
  approve       = "✅ Approve → {folder}"
  change_folder = "📁 Change folder"

  [telegram.review]
  header     = "📋 Learning Mode Review"
  what_to_do = "What should we do?"
  ```
  Accessed in code as `t("telegram.buttons.approve", folder="Invoices")` or `t("telegram.review.header")`.

---

## Variable substitution

Both file types support `{variable}` placeholders:

**In a `.txt` prompt:**
```
Classify into one of:
{folder_list}
{hint_block}
From: {from_address}
Subject: {subject}
```

**In `ui.toml`:**
```toml
[telegram.review]
ai_decision = "🧠 AI Decision: {folder} ({confidence}% · {source})"
```

**In code:**
```python
t("prompt.classifier.user",
    folder_list="Invoices, Work, Spam",
    hint_block="",
    from_address="shop@example.com",
    subject="Your invoice",
    body="...")

t("telegram.review.ai_decision",
    folder="Invoices",
    confidence=92,
    source="rule_confirmed")
```

---

## Adding a new language

1. Copy the `en/` folder and rename it to the language code:
   ```
   cp -r locales/en locales/es
   ```

2. Translate the content of each file in `locales/es/`:
   - Open each `prompt.*.txt` and translate the text
   - Open `ui.toml` and translate the **values** (keep the keys unchanged)

3. Set the environment variable in `.env` or `docker-compose.yml`:
   ```
   LANGUAGE=es
   ```

4. Any key not found in the new language automatically falls back to `en` — so you can translate incrementally, one file at a time.

---

## ui.toml sections reference

| Section | Contents |
|---------|----------|
| `telegram.buttons` | All inline keyboard button labels |
| `telegram.review` | Learning Mode review card text |
| `telegram.rule_card` | Rule configuration card text |
| `telegram.keywords` | Keyword editing prompts |
| `telegram.folder` | Folder creation and selection messages |
| `telegram.sender` | Sender type/name correction messages |
| `telegram.learning_mode` | `/learn on/off` command responses |

---

## Tips

- **Do not change the keys** in `ui.toml` or the filenames of `.txt` files — only translate the values/content.
- **Prompts are just text files** — open them in any editor, read them like a document, save when done.
- **Test your translation** by setting `LANGUAGE=xx` locally and running the service.
- If you find a hardcoded string in the code that should be translatable, add it to the locale files and replace it with `t("your.key")`.
