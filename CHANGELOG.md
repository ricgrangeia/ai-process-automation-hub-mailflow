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

## [Unreleased]

### Planned

- Alembic database migrations
- Invoice / document OCR extraction
- Supplier detection and matching
- REST API (FastAPI) for external integrations
- Webhook notifications on classification events
- Health check endpoints for Docker liveness probes
- Audit log viewer in dashboard
