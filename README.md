# MailFlow Engine

> Version 1.6.0 — Part of the [Appa8 AI Process Automation Hub](https://appa8.com)

AI-powered email automation and classification engine, built for **on-premise deployments** where full data privacy is required.

MailFlow ingests emails from IMAP or Microsoft 365 / Outlook, classifies them using a hybrid AI engine
(rules + local LLM), moves them to the correct folder automatically, and provides a web dashboard
for supervision and account management.

---

![MailFlow Engine Infographic](docs/infographic.svg)

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
| Telegram bot — NeedsReview inline review + reply | ✅ |
| Learned rules — human decisions saved, never asked again | ✅ |
| Pluggable action system — move, export PDF, extensible | ✅ |
| PDF export with path templates (`Company/{year}/{month}/`) | ✅ |
| Mobile-responsive dashboard (Chrome on phone) | ✅ |
| Natural language email search via Telegram ("send me all invoices from amazon.com January 2026") | ✅ |
| Query worker — independent service, processes search jobs from Redis queue | ✅ |
| Query delivery choice — show inline in Telegram or send by email | ✅ |
| Learning Mode — `/learn on/off` routes LLM-classified emails to human review | ✅ |
| Review worker — sends rich Telegram card: Approve / Change folder / Fix sender | ✅ |
| Sender identity — LLM extracts company/person + name in same classification call | ✅ |
| Dashboard Remetente column — shows 🏢 Amazon / 👤 João Silva | ✅ |
| Admin commands — `/status`, `/recover`, `/restart`, `/learn` with Telegram menu | ✅ |
| Alembic migrations — auto-applied on ai-worker startup, schema always up to date | ✅ |
| Audit log — every action recorded with actor, timestamp, details | ✅ |
| Dashboard Audit Log page — filterable by actor, action type, date range | ✅ |

---

## Architecture

```text
IMAP / Outlook
      │
      ▼
 email-worker / api-worker
 (fetch unseen messages, parse RFC822)
      │
      ▼
  Redis  mailai:jobs:email
      │
      ▼
   ai-worker
   ├─ Learned Rules  (DB-backed, human-confirmed)
   ├─ Rule Classifier  (fast, deterministic)
   └─ LLM Classifier   (Qwen 2.5 · extracts folder + sender identity)
      │
      ├─ confidence ≥ 0.75 + Learning Mode OFF ──▶ Actions
      │                                             ├─▶ move_folder  (IMAP)
      │                                             └─▶ export_pdf   (disk · {year}/{month})
      │
      ├─ confidence ≥ 0.75 + Learning Mode ON  ──▶ review-worker
      │   (only if not matched by learned rule)      └─▶ Telegram review card
      │                                                   ├─▶ Approve → move
      │                                                   ├─▶ Change folder → move + save rule?
      │                                                   └─▶ Fix sender → update identity
      │
      ├─ confidence < 0.75 ──▶ Telegram NeedsReview
      │                         └─▶ You reply → move + learn rule
      │
      └─▶ Store metadata + telemetry + sender identity in PostgreSQL
                        │
                        ▼
                   Dashboard
              (Streamlit · port 8501)

── Natural Language Query ───────────────────────────────
 You (Telegram): "send invoices from amazon.com Jan 2026"
      │
      ▼
  telegram-bot  (thin UI layer — just pushes the job)
      │
      ▼
  Redis  mailai:jobs:query
      │
      ▼
  query-worker
  ├─ parse_query    (LLM extracts structured filters)
  ├─ search_emails  (PostgreSQL · up to 50 results)
  └─ deliver        (inline Telegram summary  OR  SMTP email with .eml + PDF)
```

### Services

| Service | Role |
|---|---|
| `email-worker` | Polls IMAP, parses emails, enqueues jobs |
| `api-worker` | Polls Microsoft Graph (Outlook), enqueues jobs |
| `ai-worker` | Classifies emails, executes actions, runs DB migrations on startup |
| `telegram-bot` | Thin UI layer — user input, NeedsReview callbacks, admin commands |
| `review-worker` | Learning Mode — sends review cards, handles Approve / Change / Fix sender |
| `query-worker` | Natural language search — LLM parse → DB search → deliver results |
| `dashboard` | Streamlit UI — supervision + account management |
| `redis` | Job queues (AOF persistent) — `jobs:email`, `jobs:query`, `jobs:review` |
| `postgres` | Persistence (external, via `database-network`) |

### Code Structure

The codebase is organised as a **modular monolith** — each domain is a self-contained Python package. To change or extend a domain you only need to read that domain's folder.

```text
app/
├── core/                   # Shared kernel — config, crypto, database engine, migrations, audit
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
│   └── actions/            # Pluggable actions: move_folder, export_pdf
├── review/                 # Learning Mode review domain
│   ├── queue.py            # REVIEW_QUEUE_KEY + LEARNING_MODE_KEY constants
│   └── worker.py           # Redis consumer — sends Telegram review cards
├── query/                  # Natural language email search domain
│   ├── queue.py            # QUERY_QUEUE_KEY + result TTL constants
│   ├── parser.py           # LLM extracts structured filters from free text
│   ├── repository.py       # Dynamic SQLAlchemy query against emails table
│   ├── exporter.py         # SMTP email with .eml + PDF attachments
│   └── worker.py           # Redis consumer — processes search + deliver jobs
├── telegram/               # Telegram bot — thin UI layer, pushes jobs to Redis
└── dashboard/              # Streamlit UI

alembic/                    # Database migration scripts
├── env.py                  # Async engine setup, all models imported
└── versions/
    ├── 001_baseline.py              # No-op — marks existing schema
    ├── 002_add_sender_fields.py     # Adds sender_name + sender_type to emails
    └── 003_add_audit_logs.py        # Creates audit_logs table
```

**Dependency rule:** arrows flow inward toward `core/`. No domain imports another domain's internals — only its public `__init__.py` or `contracts.py`.

| To add... | You only touch... |
|---|---|
| New email source (e.g. Gmail) | `ingestion/gmail/` |
| New classifier | `classification/` |
| New processing action (webhook, forward, ticket) | `processing/actions/` |
| New query output (Slack, webhook, PDF report) | `query/exporter.py` |
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

# Telegram — optional, leave empty to disable
# Create a bot via @BotFather · get your chat_id via @RawDataBot
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# SMTP — required to send query results via email (query-worker)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@email.com
SMTP_PASSWORD=your-app-password
REPORT_RECIPIENT=recipient@email.com
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
python -m app.processing.worker          # ai-worker (classification + migrations)
python -m app.review.worker              # review-worker (Learning Mode review cards)
python -m app.telegram.bot               # telegram-bot (callbacks + admin commands)
python -m app.query.worker               # query-worker (NL search jobs)
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

- [ ] Invoice / document OCR extraction
- [ ] Supplier detection and matching
- [ ] REST API (FastAPI) for external integrations
- [ ] Webhook notifications on classification events
- [ ] Docker health check endpoints
- [ ] Learned rules dashboard page (view, edit, disable rules)
- [ ] PostgreSQL full-text search (GIN index on subject + body)
- [x] Alembic database migrations — auto-applied on startup
- [x] Redis queue durability on reboot / restart (AOF persistence)
- [x] Telegram bot — NeedsReview review, learned rules, PDF export actions
- [x] Email search via Telegram ("send me all invoices from amazon.com January 2026")
- [x] Query worker — independent service, decoupled from Telegram bot via Redis queue
- [x] Learning Mode — human review loop for LLM-classified emails
- [x] Sender identity extraction — company/person + name via LLM
- [x] Admin commands — /status, /recover, /restart, /learn
- [x] Audit log — full action trail with actor, timestamp, details; dashboard page with filters

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
