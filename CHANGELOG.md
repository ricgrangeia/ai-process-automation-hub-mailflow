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

## [Unreleased]

### Planned
- Alembic database migrations
- Invoice / document OCR extraction
- Supplier detection and matching
- REST API (FastAPI) for external integrations
- Webhook notifications on classification events
- Health check endpoints for Docker liveness probes
- Audit log viewer in dashboard
