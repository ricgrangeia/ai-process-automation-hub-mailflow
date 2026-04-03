# MailFlow Engine

> Version 1.0.0 — Part of the [Appa8 AI Process Automation Hub](https://appa8.com)

AI-powered email automation and classification engine, built for **on-premise deployments** where full data privacy is required.

MailFlow ingests emails from IMAP or Microsoft 365 / Outlook, classifies them using a hybrid AI engine (rules + local LLM), moves them to the correct folder automatically, and provides a web dashboard for supervision and account management.

---

## What's Working

| Feature | Status |
|---|---|
| IMAP email ingestion | ✅ |
| Microsoft Graph / Outlook ingestion | ✅ |
| RFC822 email parsing + attachment storage | ✅ |
| Hybrid classifier — rule-based + LLM (Qwen 2.5) | ✅ |
| Redis job queue (LPUSH / BRPOP) | ✅ |
| Auto-move emails to classified IMAP folders | ✅ |
| PostgreSQL persistence (async SQLAlchemy 2.0) | ✅ |
| ROI telemetry — token counts, processing time | ✅ |
| Fernet encryption for stored credentials | ✅ |
| Streamlit dashboard — KPIs, charts, audit log | ✅ |
| Email Accounts UI — add / toggle / delete | ✅ |
| Docker Compose — dev + production (Traefik/HTTPS) | ✅ |
| GitHub Actions → Portainer auto-deploy | ✅ |

---

## Architecture

```
IMAP / Outlook
      │
      ▼
 email-worker / api-worker
 (fetch unseen messages, parse RFC822)
      │
      ▼
  Redis Queue
      │
      ▼
   ai-worker
   ├─ Rule Classifier  (fast, deterministic)
   └─ LLM Classifier   (Qwen 2.5 · OpenAI-compatible)
      │
      ├─▶ Move email to classified IMAP folder
      └─▶ Store metadata + telemetry in PostgreSQL
                        │
                        ▼
                   Dashboard
              (Streamlit · port 8501)
```

### Services

| Service | Role |
|---|---|
| `email-worker` | Polls IMAP, parses emails, enqueues jobs |
| `api-worker` | Polls Microsoft Graph (Outlook), enqueues jobs |
| `ai-worker` | Classifies emails, moves to folder, records telemetry |
| `dashboard` | Streamlit UI — supervision + account management |
| `redis` | Job queue |
| `postgres` | Persistence (external, via `database-network`) |

### Code Structure

The codebase is organised as a **modular monolith** — each domain is a self-contained Python package. To change or extend a domain you only need to read that domain's folder.

```text
app/
├── core/                   # Shared kernel — config, crypto, database engine
│   └── database/           # Base ORM class, async engine, table init
├── accounts/               # Email accounts & API credentials (models + seed)
├── messages/               # Email messages, attachments, disk storage
├── classification/         # Classifiers: rule, LLM (Qwen 2.5), hybrid
│   └── contracts.py        # ClassificationResult — the shared boundary type
├── ingestion/
│   ├── parser.py           # RFC822 email parser (shared by all sources)
│   ├── imap/               # IMAP client + polling worker
│   └── outlook/            # Microsoft Graph client + polling worker
├── processing/             # Redis queue interface + AI worker loop
└── dashboard/              # Streamlit UI
```

**Dependency rule:** arrows flow inward toward `core/`. No domain imports another domain's internals — only its public `__init__.py` or `contracts.py`.

| To add... | You only touch... |
|---|---|
| New email source (e.g. Gmail) | `ingestion/gmail/` |
| New classifier | `classification/` |
| New processing action (webhook, forward) | `processing/` |
| New dashboard page | `dashboard/` |
| New account type | `accounts/` |

---

## Classification Categories

| Label | Trigger |
|---|---|
| `Invoices` | Rule: invoice, fatura, recibo keywords |
| `Work` | LLM |
| `Personal` | LLM |
| `Marketing` | Rule: unsubscribe, newsletter |
| `Spam` | LLM |
| `Other` | LLM default |
| `NeedsReview` | LLM confidence below threshold (0.75) |

---

## Quick Start

**1. Clone and configure**

```bash
git clone https://github.com/ricgrangeia/ai-process-automation-hub-mailflow.git
cd ai-process-automation-hub-mailflow
cp .env.example .env
```

**2. Generate a master key for credential encryption**

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the output into `.env` as `MASTER_KEY=...`

**3. Fill in the remaining `.env` values** (see [Configuration](#configuration))

**4. Start services**

```bash
make up
# or: docker compose up -d
```

**5. Open the dashboard**

- Local: `http://localhost:8501`
- Production: configured via Traefik label in `docker-compose.yml`

---

## Configuration

```env
# Infrastructure
DATABASE_URL=postgresql+asyncpg://user:password@postgres:5432/mailaiworker
REDIS_URL=redis://redis:6379/0
STORAGE_ROOT=/storage

# Credential encryption (required — generate with secrets.token_hex(32))
MASTER_KEY=change-me-to-a-random-secret

# LLM (OpenAI-compatible endpoint)
LLM_BASE_URL=http://fastapi:8000/v1
LLM_API_KEY=your-api-key
LLM_MODEL=qwen2.5-7b-instruct

# Worker behaviour (optional)
POLL_INTERVAL_SEC=240
MAX_UNSEEN_PER_CYCLE=20
INBOX_FOLDER=INBOX
MARK_SEEN_AFTER_STORE=true

# Dashboard credentials
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=mudar123
```

---

## Dashboard

After login, two pages are available from the sidebar:

**📊 Dashboard**
- Total emails processed, average AI confidence, average processing time
- Pie chart — classification distribution
- Bar chart — rule vs LLM decisions
- Audit table — last 200 processed emails

**✉️ Email Accounts**
- List all configured accounts with active/inactive status
- Add IMAP account (password encrypted at rest with Fernet)
- Add Outlook / Microsoft 365 account
- Activate / deactivate / delete accounts

---

## Development

Run workers individually:

```bash
python -m app.ingestion.imap.worker      # email-worker (IMAP)
python -m app.ingestion.outlook.worker   # api-worker (Outlook)
python -m app.processing.worker          # ai-worker (classification)
streamlit run app/dashboard/app.py       # dashboard
```

Useful Makefile commands:

```bash
make up             # Start all services
make down           # Stop all services
make build          # Rebuild images (no cache)
make restart        # down + up
make restart-local  # Local compose down + up
make logs           # Tail all service logs
make logs-ai        # Tail ai-worker logs
make shell          # Shell into ai-worker container
```

---

## Roadmap

- [ ] Alembic database migrations
- [ ] Invoice / document OCR extraction
- [ ] Supplier detection and matching
- [ ] REST API (FastAPI) for external integrations
- [ ] Webhook notifications on classification events
- [ ] Docker health check endpoints
- [ ] Audit log viewer in dashboard

See [CHANGELOG.md](CHANGELOG.md) for the full history.

---

## Tech Stack

- **Python 3.12** — AsyncIO, SQLAlchemy 2.0, httpx, tenacity
- **PostgreSQL** — asyncpg (workers) + psycopg2 (dashboard)
- **Redis** — job queue
- **Streamlit** + Plotly + Pandas — dashboard
- **Cryptography (Fernet)** — credential encryption
- **Docker Compose** + Traefik — deployment
- **GitHub Actions** + Portainer — CI/CD

---

## Author

Ricardo Grangeia — Senior Software Engineer — Portugal
<https://ricardo.grangeia.pt>

---

## License

MIT License
