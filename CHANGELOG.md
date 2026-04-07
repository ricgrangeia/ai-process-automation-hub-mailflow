# Changelog

All notable changes to MailFlow Engine are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project uses [Semantic Versioning](https://semver.org/).

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
