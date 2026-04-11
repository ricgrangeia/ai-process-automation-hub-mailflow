# Changelog

All notable changes to MailFlow Engine are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project uses [Semantic Versioning](https://semver.org/).

---

## [2.8.0] — 2026-04-11

### Added

- **Unified IMAP worker routing** — `email-worker` now polls **all** active IMAP accounts regardless of `managed_by`; routes jobs to `mailai:jobs:email` (ai_worker accounts) or `mailai:jobs:invoice` (invoice_worker accounts); for invoice accounts, runs `is_marketing()` + keyword pre-screen before storing — non-financial emails left completely untouched (unread, unmoved)
- **Invoice worker as Redis consumer** — `invoice-worker` no longer polls IMAP directly; receives jobs via `BRPOP mailai:jobs:invoice`; `_process_email_by_id()` loads the stored `EmailMessage` and `EmailAccount` from DB by `email_id` + `classification` from the job payload; full pipeline unchanged
- **Marketing email filter** (`detector.py`) — new `is_marketing_email()` function; checks `List-Unsubscribe` header (set by all bulk-mail senders per RFC 2369) and body/HTML for "unsubscribe", "opt-out", "ver no navegador", etc.; runs before keyword detection; marketing emails never trigger LLM or DB storage even if they mention "payment"; bare `"confirmation"` keyword removed from `PAYMENT_KEYWORDS` (was too broad — matched shipping/subscription confirmations)
- **NIF / seller name lookup tool** in `python-ai-api` — `GET /tools/nif/lookup?nif=` resolves Portuguese NIF to company name; DDG-first strategy: POST to `html.duckduckgo.com/html/` with PT-specific extraction patterns (eInforma "contribuinte de X", Iberinform "O NIF de X é …", Kompass "N. FISCAL …"); nif.pt fetched second for activity/CAE/address enrichment; in-process cache; `POST /tools/nif/lookup/bulk` (up to 20 NIFs in one call); `GET /tools/nif/raw` debug endpoint returning raw nif.pt HTML
- **Sellers table** (`app/sellers/`) — new `sellers` DB table with columns `nif` (unique), `name`, `activity`, `cae`, `address`, `situation`, `created_at`, `updated_at`; Alembic migration `016_add_sellers_table.py`; model registered in `core/database/init.py` for `create_all`
- **Seller name enrichment** — `app/invoices/nif_lookup.py` calls `ai-api /tools/nif/lookup` on cache miss; upserts into `sellers`; backfills `invoices.seller_name` for existing rows; each DB operation (`_db_lookup`, `_db_upsert`, `_db_update_invoices`) is independently error-handled — a missing table or DB failure on one step does not block the others
- **`seller_name` column in AT Invoices dashboard tab** — resolved company name shown alongside NIF seller
- **🏪 Sellers dashboard page** — paginated `st.data_editor` with `🗑️` inline-delete column (same pattern as invoices table), search filter (NIF / name / activity), manual NIF lookup expander, CSV export; navigation between Companies and Invoices
- **Invoices dashboard account filter** — selectbox to scope all KPIs, charts, and tables to a single email account; "All accounts" shows aggregate
- **Invoices dashboard monthly totals chart** — bar chart showing gross invoice total per calendar month, displayed below KPI metrics
- **Top 5 sellers chart fix** — NIF values were abbreviated as "501M" due to Plotly treating them as numerics; fixed with `xaxis_type="category"` + `category_orders`; labels now show seller name + NIF; "Others" bar removed

### Changed

- `email-worker` — removed `managed_by = ai_worker` filter from account query; added routing logic: invoice_worker accounts go through marketing + keyword pre-screen before storing
- `invoice-worker` — removed all IMAP polling code; now a pure Redis consumer; `_process_email_by_id()` is the main entry point
- `detector.py` — `is_marketing_email()` added as first gate in `classify_email()`; bare `"confirmation"` removed from `PAYMENT_KEYWORDS`

### Fixed

- `/v1/v1/chat/completions` 404 in `body_extractor.py` — `llm_base_url` already ends with `/v1` on some configs; fixed with `base = llm_base_url.rstrip("/").removesuffix("/v1")`
- `immutabledict is not a sequence` in dashboard testing tools — `pd.read_sql` with SQLAlchemy 2.x passes immutabledict params to psycopg2; replaced with `_sql()` helper using `engine.connect()` + `text()` + manual DataFrame construction

---

## [2.7.0] — 2026-04-09

### Added

- **Full i18n** — all dashboard pages, Telegram notifications, and query worker support `LANGUAGE=pt/en/de` env var; locale files in `locales/{lang}/ui.toml`; missing keys fall back to English
- **Dashboard login persistence** — cookie-based session; user stays logged in across page refreshes without re-entering credentials
- **Keyword extractor improvements** — preserves accented Portuguese characters; rejects codes and digit strings; real words only

### Changed

- LLM prompts enforce exact folder names from the DB; no English translation of folder names by the model

---

## [2.6.0] — 2026-04-09

### Added

- **Invoice Worker service** — dedicated Docker service (`invoice-worker`) for accounts with `managed_by = invoice_worker`; operates independently of the AI classification pipeline
- **`managed_by` column** on `email_accounts` — values: `ai_worker` (default) or `invoice_worker`; Alembic migration `015`
- **Per-account worker mode UI** — dashboard Email Accounts page allows switching worker mode per account; change takes effect on next poll cycle
- **Body-only financial detection** — invoice-worker uses two-stage keyword scan + LLM extraction for emails without PDF attachments (payment confirmations, bank transfers, store receipts)
- **Payment extraction gate** — non-payment document types (ND, NC, GR, GT) skip Multibanco payment extraction step
- **ATCUD QR selection** — picks the first QR code containing AT field H; ignores decorative/tracking QR codes
- **Invoice Worker Collaborative Mode** — one mailbox can handle both personal email (ai_worker) and automated invoice processing (invoice_worker); non-financial emails on invoice accounts are left untouched

---

## [2.5.0] — 2026-04-09

### Added

- **International invoice support** — non-PT-AT PDFs (no ATCUD) extracted via LLM; captures `seller_name`, `seller_country`, `currency`, `vat_rate`, `receipt_number`, `card_last4`, `payment_method`; Alembic migration `014`
- **`invoice_origin` field** — classifies the financial document type: `pt_at`, `international`, `payment_confirmation`, `bank_transfer`, `receipt`
- **Payment method field** — `card / bank_transfer / mbway / multibanco / paypal` stored per invoice
- **Invoice dashboard two-tab layout** — `🇵🇹 AT Invoices` tab (QR/ATCUD data) + `🌍 Foreign` tab (international); separate KPI rows, columns, and CSV export per tab
- **LLM routing via ai-api** — mailflow routes all LLM calls through `ai-api /v1/chat/completions` instead of vLLM directly; enables full message history support and LangGraph tool routing

---

## [2.4.0] — 2026-04-08

### Added

- **Multibanco payment extraction** — invoice-worker LLM reads PDF text layer and extracts MB entity, reference, amount, and due date; stored in `invoices` table; Alembic migration `007`
- **Invoice deduplication** — three-level dedup: `(nif_seller, invoice_number)` unique key → ATCUD fallback → `email_id` fallback; Alembic migration `008`
- **`document_type` field** — AT QR field D parsed and stored (FT, FS, FR, ND, NC, GR, GT…); Alembic migration `012`
- **`document_type_description`** — human-readable label derived from AT code (e.g. FT → "Fatura"); Alembic migration `013`

---

## [2.3.0] — 2026-04-08

### Added

- **Invoice QR extraction** — when an email is classified as Invoices and has PDF attachments,
  `ExportPdfAction` calls the AI Tool Server `/tools/pdf/qr/decode-base64` endpoint,
  parses the Portuguese AT/ATCUD QR format, and persists structured data to a new `invoices` table
- **`app/invoices/` domain** — `models.py` (Invoice ORM), `qr_parser.py` (AT QR field parser),
  `extractor.py` (HTTP call + upsert)
- **Alembic migration 006** — creates `invoices` table with indexes on `email_id` and `nif_seller`
- **`🧾 Invoices` dashboard page** — KPI row (gross total, VAT, taxable base, unique sellers),
  top-sellers bar chart, filterable table (date range, NIF, invoice number), CSV export
- **`TOOL_SERVER_URL` + `TOOL_SERVER_API_KEY`** settings — optional; feature silently disabled when empty
- **`session_factory` injected into action config** — allows actions to persist data without
  additional arguments to `execute()`

### Changed

- `ExportPdfAction` now accepts `session_factory` from the action config and triggers QR extraction
  after copying PDF attachments (only when folder label contains "invoice" / "fatura")
- `run_actions` in `processing/worker.py` enriches each action config dict with `session_factory`
  before instantiation

---

## [2.2.0] — 2026-04-08

### Added

- **Telegram "➕ New folder" button** on every NeedsReview card (standard, conflict, suggestion variants)
  — user types a folder name, it is created in DB, on all IMAP accounts, and the email is moved immediately
- **`folder_new_request` callback** in `bot.py` — stores pending state per chat, resolved on next text message
- **Subfolder support** via IMAP hierarchy separator auto-detection (`_get_imap_separator`)
  — canonical names use `/` (e.g. `Work/Clients`); converted to `.` for Dovecot/cPanel servers automatically
- **`_normalize_folder`** helper in `imap/client.py` — used by `move_message`, `rename_imap_folder`, `ensure_folder_exists`
- **Dashboard folder create** now also creates the IMAP label on all active accounts
- **Dashboard folder delete** now also deletes the IMAP label on all active accounts (with separator normalisation)
- **`handle_folder_suggest_add`** now creates the IMAP folder on all accounts (was DB-only before)
- **Query search by `sender_name` and `sender_type`** — LLM prompt updated with examples;
  repository filters added; `_EmailProxy` and Redis payload include both fields
- **Folder list in query parser** fetched from DB at query time — LLM prompt always reflects current folders
- **LLM inference time (`processing_time_seconds`)** now saved for all email paths
  (Learning Mode and NeedsReview paths previously exited before the DB update)

### Fixed

- `decrypt_secret` argument order was swapped in dashboard IMAP rename — caused `InvalidToken` crash
- `PYTHONUNBUFFERED=1` missing from `Dockerfile.dashboard` — `print()` output was buffered, never visible in `docker logs`
- `logging.basicConfig` missing from `dashboard/app.py` — `imap-worker` logger output was silently discarded
- Dashboard IMAP rename used substring match (`old_name in f.decode()`) — now uses exact parsed name via `_list_imap_folder_names`
- Dashboard IMAP rename/delete/create results were rendered before `st.rerun()` — moved to `st.session_state` so they persist across the rerun and are displayed at top of page

---

## [1.0.0] — 2026-04-03

First stable release. Core email pipeline, AI classification, and dashboard are fully operational.

### Added

- IMAP email ingestion with polling loop (`email-worker`)
- Microsoft Graph / Outlook ingestion (`api-worker`)
- RFC822 email parsing with multipart and attachment support
- Hybrid AI classifier: rule-based (fast) + LLM (Qwen 2.5 via OpenAI-compatible API)
- Redis job queue — LPUSH enqueue / BRPOP blocking consumption
- AI worker that classifies emails and moves them to labelled IMAP folders
- PostgreSQL persistence with SQLAlchemy 2.0 async ORM
- ROI telemetry: prompt tokens, completion tokens, processing time per email
- Fernet encryption for credentials stored in the database (`MASTER_KEY`)
- Streamlit dashboard with login, KPI metrics, classification charts, audit table
- Email Accounts management UI: add/toggle/delete IMAP and Outlook accounts
- Docker Compose for local development and production (Traefik + HTTPS)
- GitHub Actions CI → Portainer webhook auto-deploy

### Fixed

- `imap_worker.py` `mark_seen`: missing closing `)` in IMAP `\Seen` flag
- `hybrid_classifier.py`: missing `ClassificationResult` import causing runtime crash
- `crypto.py`: encryption was disabled — plaintext credentials stored in DB
- `api_worker.py` / `seed_outlook_credentials.py`: hardcoded `"master-key-disabled"` replaced with `settings.master_key`
- `Makefile`: stale Yii2/PHP container references replaced with correct service names

---

## [1.1.0] — 2026-04-04

### Changed

- Restructured `app/` from a flat layout into a modular domain architecture
- Each domain (`accounts`, `messages`, `classification`, `ingestion`, `processing`, `dashboard`) is now a self-contained package — changes to one domain no longer require reading others
- Shared kernel extracted to `app/core/` (config, crypto, database engine, SQLAlchemy Base)
- `app/models.py` split into `app/accounts/models.py` and `app/messages/models.py`
- `app/classifier/` moved to `app/classification/` with `base.py` renamed to `contracts.py`
- `app/imap_worker.py` → `app/ingestion/imap/client.py`
- `app/main.py` → `app/ingestion/imap/worker.py`
- `app/outlook_graph.py` → `app/ingestion/outlook/client.py`
- `app/api_worker.py` → `app/ingestion/outlook/worker.py`
- `app/mail_parser.py` → `app/ingestion/parser.py`
- `app/ai_worker.py` → `app/processing/worker.py`
- `app/queue.py` → `app/processing/queue.py`
- `app/storage.py` → `app/messages/storage.py`
- `app/dashboard.py` → `app/dashboard/app.py`
- `app/seed_outlook_credentials.py` → `app/accounts/seed.py`
- Docker Compose and Dockerfile entry points updated to new module paths
- All imports converted from relative to absolute (`app.domain.module`)

---

## [1.1.1] — 2026-04-04

### Fixed

- `processing/worker.py`: IMAP move was using `account.password_encrypted` directly instead of decrypting it — caused `AUTHENTICATIONFAILED` on every classification job
- `processing/worker.py`: added startup recovery — on boot, re-enqueues emails stuck with `status='new'` older than 2 minutes, self-healing after crashes or redeploys
- `processing/worker.py`: added in-flight retry — failed jobs are re-queued up to 3 times before being marked `failed_retries`; queue key centralised to `QUEUE_KEY` constant
- `processing/worker.py`: Redis queue key was hardcoded in three places — now imported from `processing/queue.py`
- `ingestion/parser.py`: email subjects stored as raw RFC 2047 encoded-word strings (`=?UTF-8?Q?...?=`) — now decoded at parse time using `email.header.decode_header`
- `dashboard/app.py`: RFC 2047 encoded subjects already in the DB are decoded before display
- `dashboard/app.py`: confidence column now displays as percentage (`95%`, `100%`) instead of raw float (`0.95`, `1`)
- `dashboard/app.py`: `sys.path` injection replaced with `Path(__file__).resolve()` and `insert(0, ...)` — fixes "module not found" on dashboard startup
- `docker-compose.yml` / `docker-compose.local.yml`: Redis AOF persistence enabled (`--appendonly yes`) — queue jobs now survive container restarts and reboots
- `docker-compose.local.yml`: stale module entry points updated to new modular paths
- `dashboard/app.py`: added responsive mobile CSS — columns stack vertically, touch targets enlarged, tables horizontally scrollable on screens ≤ 768px

---

## [1.2.0] — 2026-04-05

### Added

- Telegram bot integration (`app/telegram/`) — NeedsReview emails trigger an inline-button message asking for human classification
- Telegram bot service (`app/telegram/bot.py`) — runs as a standalone Docker container, handles callback replies, moves email via IMAP, updates DB with `ai_source=human`
- Startup notification — AI worker sends a Telegram message when it comes online
- After classifying via Telegram, user is asked whether to save the decision as a learned rule (rule storage coming in next release)
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` added to `Settings` and `.env.example` — feature is silently disabled when left empty
- `telegram-bot` service added to both `docker-compose.yml` and `docker-compose.local.yml`
- Project infographic (`docs/infographic.svg`) added to README — dark-themed SVG showing the full pipeline

### Changed

- `processing/worker.py`: NeedsReview emails with Telegram configured now set `status=pending_review` and skip IMAP move until human replies; startup recovery explicitly excludes `pending_review` emails
- `docker-compose.local.yml`: `ai-worker` was missing `MASTER_KEY` — added

---

## [1.3.0] — 2026-04-05

### Added

- `LearnedRule` model (`classification/learned_rules.py`) — stores human-confirmed decisions as DB rules with a JSON `actions` column; upserts on duplicate domain
- Pluggable action system (`processing/actions/`) — `EmailAction` base interface + factory registry; new actions require only one new file
- `MoveFolderAction` (`processing/actions/move_folder.py`) — extracted IMAP move logic into a reusable action
- `ExportPdfAction` (`processing/actions/export_pdf.py`) — exports email body as PDF and copies PDF attachments to a structured path; supports `{year}`, `{month}`, `{day}` template variables (e.g. `Company/{year}/{month}/Payments/`)
- `weasyprint==62.3` added to `requirements.txt` for PDF rendering
- Telegram bot: after classifying a NeedsReview email, user is now asked to choose an action rule — *Move only*, *Export PDF only*, *Move & Export*, or *Just this once*
- Telegram bot: custom PDF export path can be typed as a free-text reply or accepted from a default; state held in-memory between messages
- `RuleClassifier` now accepts an optional `session_factory` — when provided, checks learned rules from DB before hardcoded patterns and increments `hit_count` on match
- `processing/worker.py`: `run_actions()` helper looks up learned rule actions for each classified email and executes them in order; falls back to plain `move_folder` if no rule found
- `core/database/init.py`: `LearnedRule` registered so table is auto-created on boot

### Changed

- `processing/worker.py`: IMAP move logic removed from inline closure — now delegated to `MoveFolderAction` via `run_actions()`
- `telegram/bot.py`: `handle_learn` stub replaced with full rule persistence + action-choice flow

---

## [1.4.0] — 2026-04-05

### Added

- Natural language email search via Telegram — type "send me all invoices from amazon.com January 2026" and receive a results email
- `app/query/` domain — fully self-contained search module:
  - `parser.py` — calls local LLM (Qwen 2.5) to extract structured filters (`sender_domain`, `folder`, `date_from`, `date_to`, `keyword`) from free text;
    resolves relative dates ("last month") using today's date
  - `repository.py` — dynamic SQLAlchemy query against `emails` table; supports all filter combinations; returns up to 50 results ordered by date
  - `exporter.py` — builds MIME email with plain-text summary in body, attaches original `.eml` files and PDF attachments from disk, sends via SMTP with STARTTLS
  - `worker.py` — standalone Redis consumer (`mailai:jobs:query`); runs independently of the Telegram bot; sends Telegram reply when done; falls back to inline chat summary if SMTP is not configured
- `/search <query>` Telegram command as explicit alternative to free-text queries
- SMTP settings (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `REPORT_RECIPIENT`) added to `Settings` and `.env.example`
- `query-worker` Docker service added to both `docker-compose.yml` and `docker-compose.local.yml`

### Changed

- `telegram/bot.py` is now a thin UI layer — on receiving a search query it pushes a job to Redis and replies "On it…"; all query logic moved to `query/worker.py`
- `telegram-bot` Docker service no longer requires LLM or SMTP environment variables
- `query-worker` owns LLM and SMTP configuration — each service only has the vars it needs

---

## [2.1.1] — 2026-04-08

### Fixed

- **Dashboard Tempo(s) column showing "None"** — the apply lambda returned Python `None` for null values,
  which Streamlit renders as the text "None"; changed to return `"1.23s"` for valid values and `"—"` for
  nulls; removed `NumberColumn` format config for that column (now a plain text column)
- **Tempo(s) metric using wrong column state** — the KPI metric was reading `df['Tempo(s)']` after it had
  already been converted to strings by the apply; now reads the raw numeric column via `pd.to_numeric`
  before the apply runs
- **Tempo(s) measured total job time instead of model inference time** — `processing_time_seconds` was set to
  `time.time() - start_time` which included DB queries, IMAP move, and everything else; now measures only
  the HTTP roundtrip to the LLM (from sending the request to receiving the response):
  - `ClassificationResult.llm_time_seconds` field added (float, seconds, default 0.0)
  - `LLMClassifier.classify()` wraps the `httpx.post()` call with `time.perf_counter()` and stores the
    delta in `result.llm_time_seconds`
  - `HybridClassifier.classify()` propagates `llm_time_seconds` through the conflict and
    low-confidence result paths
  - `processing/worker.py` stores `classification.llm_time_seconds` as `processing_time_seconds`;
    `None` when no LLM ran (rules_only mode, human-reclassified emails); removed unused `start_time`
    and `time` import

---

## [2.1.0] — 2026-04-08

### Added

- **Dynamic folder management** (`app/folders/`) — folders are now stored in a `folders` DB table instead of being hardcoded throughout the codebase:
  - `Folder` SQLAlchemy model with `name`, `is_active`, `created_at`
  - `get_active_folder_names(session)` repository helper — async, falls back to defaults if table not yet migrated
  - Alembic migration `005_add_folders.py` — creates table and seeds default folders (Invoices, Work, Personal, Marketing, Spam, Other)
- **Dashboard — 📁 Folders page** — full CRUD management:
  - List all folders with active/disabled status and creation date
  - **Enable / Disable** toggle — removes folder from LLM prompt and Telegram buttons without deleting it
  - **Rename** — updates DB, renames `emails.classification_label` for all affected emails, and runs IMAP `RENAME` on every active IMAP account; per-account success/failure reported inline
  - **Delete** — blocked if any emails still reference the folder (prevents orphaned records); suggest disabling instead
  - **➕ Add** — create a new folder; immediately available to the LLM and Telegram buttons
  - All changes audited (`folder.created`, `folder.toggled`, `folder.renamed`, `folder.deleted`)
- **AI folder suggestions (Option C)** — when the LLM classifies an email into a folder name that doesn't exist in the DB, the system preserves the suggestion instead of silently routing to NeedsReview:
  - `ClassificationResult.suggested_folder` field — carries the unknown folder name through the pipeline
  - `send_review_request` renders a distinct **🆕 New folder suggested** Telegram card with the AI's proposed name,
    confidence, a **"➕ Add 'X' & move"** button, and the full existing folder list below
  - `handle_folder_suggest_add` Telegram callback handler — creates the folder in DB (idempotent), moves the email
    via IMAP, updates email status, audits as `folder.created_from_suggestion`
- `rename_imap_folder(conn, old_name, new_name)` added to `app/ingestion/imap/client.py` — wraps IMAP `RENAME` command; returns `True`/`False`; handles missing folder gracefully

### Changed

- **LLM prompt now uses the live folder list** — `LLMClassifier.classify()` accepts `folders: list[str] | None`;
  when provided, builds the "Classify into one of:" line dynamically from the DB instead of hardcoded names
- **HybridClassifier** — `classify()` accepts `folders` and passes it to both LLM calls (rule-hint path and no-rule path)
- **`processing/worker.py`** — fetches `active_folders` from DB in the same session that loads the email;
  passes to `classify()` and `send_review_request()`; validates returned folder is in the active list
- **`review/worker.py`** — loads active folders from DB and passes to `_send_review_card()` for dynamic keyboard
- **`notifications.py`** — `send_review_request` accepts `folders` and `suggested_folder`; folder keyboard built from DB list; new suggestion card variant when `suggested_folder` is set
- **`telegram/bot.py`** — `handle_rv_folder` fetches folder list from DB; removed hardcoded `FOLDERS` constant
- **Dashboard Learned Rules page** — folder selectboxes now load from DB via `_get_folder_names(engine)`
- `alembic/env.py` — `app.folders.models` imported so Alembic detects `Folder` in autogenerate

### Fixed

- **Telegram 400 Bad Request on review cards** — `review/worker.py` and `notifications.py` were sending messages
  with `parse_mode="Markdown"`; subjects and addresses containing underscores caused silent rejection;
  removed `parse_mode` from all messages that include user-supplied content
- **`/recover` not triggering re-classification** — the command reset `status='new'` in DB but never pushed jobs
  to Redis; ai-worker startup recovery excludes `pending_review` so emails were permanently stuck; fixed by
  adding `r.lpush(EMAIL_QUEUE_KEY, payload)` for each recovered email using `RETURNING id, tenant_id`
- **`telegram/bot.py` — sender name Markdown crash** — `f"✅ Sender name saved: *{text}*"` with `parse_mode="Markdown"` would fail when the user typed a name containing underscores; removed `parse_mode`

---

## [2.0.0] — 2026-04-07

### Changed (Breaking — classification behaviour)

- **Rules are no longer hard overrides.** The LLM now always runs, using the matched rule as a hint for
  validation — catches exceptions like same sender domain but different email type (invoice vs newsletter).
- **New classification sources:**
  - `rule_confirmed` — rule matched AND LLM agreed; confidence boosted to ≥ 0.95; email moved automatically
  - `rule_conflict` — rule and LLM disagree; email routed to Telegram for human decision
  - `rule` — hardcoded pattern match (LLM validation not yet applied to hardcoded rules)
- **`hybrid_classifier.py`** — complete rewrite of orchestration logic:
  - Rule match → `llm.classify(email, rule_hint=folder)` — LLM receives the rule suggestion in its prompt
  - Agreement → `source="rule_confirmed"`, confidence boosted
  - Disagreement → `ClassificationResult("NeedsReview")` with `rule_folder` and `llm_folder` set
  - No rule → pure LLM unchanged
- **`llm_classifier.py`** — new `rule_hint` parameter; when provided, injects a validation context block
  into the user prompt explaining what the rule expects and asking the model to agree or flag a conflict
- **`classification/contracts.py`** — `ClassificationResult` now has typed defaults for all attributes
  (`source`, `sender_type`, `sender_name`, token counts, `rule_folder`, `llm_folder`) — no more implicit
  dynamic attribute setting
- **`rule_classifier.py`** — hardcoded and learned rule results now explicitly set `source="rule"`
- **`telegram/notifications.py`** — `send_review_request` now accepts `source`, `rule_folder`,
  `llm_folder`; when `source="rule_conflict"` shows a distinct conflict card:

  ```text
  ⚠️ Rule Conflict — human input needed
  📚 Learned rule says: Invoices
  🧠 AI says: Marketing (82%)
  Which is correct?
  ```

- **`processing/worker.py`** — passes `source`, `rule_folder`, `llm_folder` from classification result
  to `send_review_request`
- `rules_only` operation mode is unchanged — still hard override, LLM never runs

### Fixed

- `telegram/bot.py` — `update(EmailMessage)` crashed with `TypeError: 'Update' object is not callable`;
  the SQLAlchemy `update` import was shadowed by the Telegram `Update` handler parameter; fixed by
  aliasing to `sa_update`

---

## [1.9.0] — 2026-04-07

### Added

- **Unit test suite** (`tests/unit/`) — 62+ tests covering the critical classification pipeline, no external services required:
  - `test_crypto.py` — encrypt/decrypt round-trip, wrong key, tampered token, unicode, empty string
  - `test_rule_classifier.py` — all 4 hardcoded rules, all 4 learned match fields (`sender_domain`, `sender_email`, `subject_contains`, `body_contains`), case-insensitivity, DB mocked with `unittest.mock`
  - `test_hybrid_classifier.py` — rule-first priority, threshold boundary (0.74 / 0.75 / 0.76), custom threshold, rule always beats high-confidence LLM
  - `test_llm_classifier.py` — happy path, JSON-in-prose extraction, confidence clamping, sender field normalisation, all error paths (HTTP 500, malformed JSON, empty choices, network error)
  - `test_operation_mode.py` — all 4 modes, invalid mode error, garbage Redis value fallback, using `fakeredis`
  - `test_auto_save_rule.py` — every generic domain skipped (parametrized), human rule never overwritten, ai_auto hit_count increment, new rule fields validated
- `pytest.ini` — `asyncio_mode = auto`, `testpaths = tests`
- `requirements-dev.txt` — test-only deps (`pytest==8.1.1`, `pytest-asyncio==0.23.6`, `fakeredis==2.21.3`), not installed in Docker images
- `tests/conftest.py` — shared `FakeEmail` and `FakeSettings` fixtures

### Changed

- **GitHub Actions CI** (`.github/workflows/trigger-portainer.yml`) — restructured from a single deploy-only job into a two-job pipeline:
  - `test` job — runs `pytest` on every push to any branch and on every PR to `master`
  - `deploy` job — triggers Portainer webhook only when `test` passes AND the push is to `master`
  - Pip dependency caching on `requirements.txt` + `requirements-dev.txt` for faster CI runs
  - Broken code can no longer reach production — Portainer is never called if any test fails

---

## [1.8.0] — 2026-04-07

### Added

- **Operation Mode system** — four runtime modes, switchable without restarting any service:
  - `hybrid` (default) — rules first, LLM fallback, same behaviour as before
  - `rules_only` — only learned rules fire; unmatched emails go to NeedsReview; zero LLM cost
  - `llm_only` — always calls LLM, skips rule lookup; useful for auditing model quality
  - `auto_learn` — hybrid + high-confidence LLM decisions (≥ 0.90) auto-saved as `ai_auto` learned rules
- **Auto-learn rule saving** (`_auto_save_rule`) — in `auto_learn` mode, high-confidence decisions are
  saved as `sender_domain` learned rules; skips generic domains (gmail.com, outlook.com, etc.); never
  overwrites human rules; increments hit count if the rule already exists
- **`source` field on `learned_rules`** — distinguishes `"human"` (Telegram/dashboard) from `"ai_auto"` (auto-saved); Alembic migration `004_add_learned_rules_source.py`
- **Dashboard — operation mode selector** — sidebar dropdown shows current mode with emoji labels; writes to Redis on change; audits as `mode.changed` with from/to fields
- **Telegram `/status`** — now includes current Operation Mode below the queue depths
- **`mode.changed`** audit event — recorded when dashboard changes the operation mode

### Changed

- `processing/worker.py`: classification is now mode-aware — reads `op_mode` from Redis once per job;
  `rules_only` skips LLM entirely; `llm_only` bypasses rule lookup; `hybrid`/`auto_learn` use the
  existing `HybridClassifier`; `op_mode` recorded in `email.classified` audit event
- `app/core/operation_mode.py`: `OPERATION_MODE_KEY`, `MODES`, `AUTO_LEARN_CONFIDENCE_THRESHOLD = 0.90`, `GENERIC_DOMAINS` set

---

## [1.7.0] — 2026-04-07

### Added

- **Dashboard — 📚 Learned Rules page** — full CRUD management of learned rules without touching the database:
  - Cards show match condition (`🌐 sender_domain`, `📧 sender_email`, `📝 subject_contains`, `📄 body_contains`),
    action summary (`📁 Move → Invoices  |  📄 PDF → path`), hit count, tenant, and creation date
  - **Enable / Disable** toggle per rule — disabling a rule keeps it for reference without deleting it
  - **✏️ Edit** expander — change match field, match value, target folder, and PDF export path inline
  - **🗑️ Delete** button — permanently removes the rule
  - **➕ Add Rule Manually** form at the bottom — create rules without needing to go through Telegram
  - All changes (toggle, edit, delete, add) are written to the audit log
- `rule.toggled`, `rule.updated`, `rule.deleted` added to audit log action filter and icon map in dashboard

---

## [1.6.0] — 2026-04-07

### Added

- **Audit log system** — every significant action is recorded in `audit_logs` with actor, timestamp, and context:
  - `app/core/audit.py` — `log_audit()` (async, for workers/bot) and `log_audit_sync()` (sync, for dashboard/migrations)
  - `_telegram_actor()` helper formats Telegram user as `Full Name (@username)` or `id:12345`
  - Alembic migration `003_add_audit_logs.py` — creates `audit_logs` table with indexes on `created_at`, `action`, `actor_name`
- **Audited actions:**
  - `email.classified` — ai-worker classified and moved an email (source, folder, confidence, sender identity)
  - `email.reclassified` — Telegram user changed the folder via NeedsReview or Learning Mode review card
  - `email.approved` — Telegram user approved the AI decision in Learning Mode
  - `email.sender_corrected` — Telegram user fixed sender name via review card
  - `rule.created` — Telegram user saved a new learned rule (domain, folder, actions)
  - `query.searched` — Telegram user ran a natural language email search (query text recorded)
  - `system.restart` — Telegram user sent `/restart` (targets logged)
  - `system.recover` — Telegram user sent `/recover` (reset email IDs logged)
  - `system.learning_mode` — Telegram user toggled `/learn on` or `/learn off`
  - `account.added` — Dashboard user added an IMAP or Outlook account
  - `account.toggled` — Dashboard user activated or deactivated an account
  - `account.password_changed` — Dashboard user reset an account password
  - `db.migrated` — Alembic applied migrations at startup (from/to revision recorded)
- **Dashboard — 📋 Audit Log page** — filterable by actor name, action type, and date range (last N days);
  displays up to 500 events with colour-coded action icons; gracefully shows setup hint if table doesn't exist yet
- Audit log write is **silently skipped** if `audit_logs` table doesn't exist yet (safe on first boot before migration 003)

---

## [1.5.0] — 2026-04-07

### Added

- **Learning Mode** — `/learn on/off` Telegram command; when ON, emails classified by the LLM (not a learned rule) are routed to human review before being moved, so the AI learns from every correction
- **Review domain** (`app/review/`) — fully self-contained review module:
  - `queue.py` — `REVIEW_QUEUE_KEY` and `LEARNING_MODE_KEY` constants (Redis flag `mailai:learning_mode`)
  - `worker.py` — standalone Redis consumer that sends a rich Telegram review card with inline buttons per email
- **Review card flow** — three actions per card:
  - *Approve* — accept AI decision, move email immediately
  - *Change folder* — pick correct folder from inline list; optionally save as a learned rule for that sender domain
  - *Fix sender* — pick type (company / person) then free-type the name; updates DB record
- **Sender identity extraction** — LLM extracts `sender_type` ("company" / "person") and `sender_name` (e.g. "Amazon", "João Silva") in the same classification call, zero extra inference cost
- `sender_name` (TEXT) and `sender_type` (VARCHAR 16) columns added to `emails` table
- **Dashboard Remetente column** — shows `🏢 Amazon` / `👤 João Silva` instead of raw email addresses
- **Admin Telegram commands** with Telegram menu (set via `set_my_commands` on bot startup):
  - `/status` — DB counts (total, moved, pending review) + Redis queue depths
  - `/recover` — resets all `pending_review` emails back to `new` and re-enqueues them (fixes lost button taps during bot downtime)
  - `/restart` — sends a poison-pill restart signal to all workers (email, ai, query, review); Docker restarts each automatically
  - `/learn on|off` — toggle Learning Mode
- **Query delivery choice** — before delivering search results the bot asks inline: *Show here* (Telegram summary) or *Send by email* (SMTP); result stored in Redis (10 min TTL)
- **Alembic database migrations** — full setup with async engine support:
  - `alembic.ini` — config at project root, DATABASE_URL read from environment
  - `alembic/env.py` — async SQLAlchemy engine, all models imported for `--autogenerate`
  - `alembic/versions/001_baseline.py` — no-op marking existing schema
  - `alembic/versions/002_add_sender_fields.py` — adds `sender_name` and `sender_type`
  - `app/core/migrations.py` — `run_migrations()` helper called on ai-worker startup
  - ai-worker runs `alembic upgrade head` automatically on every start
- `review-worker` service added to both `docker-compose.yml` and `docker-compose.local.yml`

### Fixed

- `classification/llm_classifier.py`: Authorization header was `x-api-key` — vLLM requires `Authorization: Bearer`; caused 401 on every LLM classification call
- `query/parser.py`: same Bearer auth fix; timeout increased 30 s → 120 s; `ast.literal_eval` fallback for
  single-quoted JSON from Qwen; null normalisation; all instructions moved from system prompt to user message
  (FastAPI wrapper was stripping the system prompt); few-shot examples added (required for Qwen 2.5 to
  reliably return clean JSON)
- `telegram/bot.py`: filter summary with underscores caused Telegram 400 Bad Request with Markdown parse mode — removed italic markers from filter summary text
- `processing/worker.py`: Learning Mode check correctly excludes emails already matched by a learned rule (`ai_source == "rule"`) to avoid re-reviewing already-known senders
- `query/worker.py` container: `/storage` volume was not mounted — query results had 0 attachments; fixed in compose files

## [Unreleased]

### Planned

- Invoice / document OCR extraction
- Supplier detection and matching
- REST API (FastAPI) for external integrations
- Webhook notifications on classification events
- Health check endpoints for Docker liveness probes
- Audit log viewer in dashboard
- Learned rules dashboard page (view, edit, disable rules)
