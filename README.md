# MailFlow Engine

> Version 2.6.0 — Part of the [Appa8 AI Process Automation Hub](https://appa8.com)

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
| Dashboard Learned Rules page — view, enable/disable, edit, delete, add manually | ✅ |
| Operation Mode system — hybrid / rules_only / llm_only / auto_learn | ✅ |
| Auto-learn — high-confidence LLM decisions auto-saved as `ai_auto` learned rules | ✅ |
| Dashboard operation mode selector — live switch without restart | ✅ |
| `/status` shows current operation mode | ✅ |
| Unit test suite — 62+ tests, no external services required | ✅ |
| CI pipeline — tests gate every push; deploy to Portainer only if tests pass | ✅ |
| Rule validation — LLM always confirms rule matches; conflicts routed to human | ✅ |
| Rule conflict card — Telegram shows rule vs AI disagreement for human decision | ✅ |
| Dynamic folder management — dashboard CRUD + IMAP rename/create/delete on all accounts | ✅ |
| AI folder suggestions — LLM proposes new folders via Telegram card with one-click create | ✅ |
| Subfolder support — use `/` separator (e.g. `Work/Clients`), server separator auto-detected | ✅ |
| Telegram "➕ New folder" on every NeedsReview card — type a name, creates in DB + IMAP + moves | ✅ |
| Query search by sender_name and sender_type (individual / company) | ✅ |
| LLM time tracking — dashboard Tempo(s) shows LLM inference time for all email paths | ✅ |
| Invoice QR extraction — PDF attachments on any classified folder decoded via AI Tool Server | ✅ |
| Invoice payment data — Multibanco entity, reference, amount and due date extracted via LLM | ✅ |
| Invoice deduplication — (nif_seller, invoice_number) → ATCUD → email_id, three-level dedup | ✅ |
| Invoice document_type — AT field D parsed and stored (FT, FS, FR, ND, NC, GR, GT…) | ✅ |
| Payment extraction gate — payment step skipped for non-payment doc types (ND, NC, GR, etc.) | ✅ |
| ATCUD QR selection — picks first QR with field H; skips decorative/tracking QR codes | ✅ |
| Invoices dashboard page — KPIs (gross, VAT, taxable), seller chart, filterable table, CSV export | ✅ |
| Invoices dashboard delete — select and permanently remove invoice records with confirmation | ✅ |
| LLM routing via AI API — mailflow routes LLM calls through ai-api instead of vLLM directly | ✅ |
| Full i18n — dashboard, Telegram notifications, query worker; `LANGUAGE=pt` env var | ✅ |
| Dashboard login persistence — cookie-based session, survives page refresh without re-login | ✅ |
| Keyword extractor — real words only: preserves accented chars, rejects codes and digit strings | ✅ |
| LLM folder names — prompts enforce exact folder names, no translation to English | ✅ |

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
   ai-worker  [Operation Mode — reads from Redis per job]
   │
   ├─ hybrid (default)  Rule as hint → LLM always validates
   ├─ rules_only        Only learned rules · unmatched → NeedsReview · zero LLM cost
   ├─ llm_only          Always LLM · skips rules · model quality audit
   └─ auto_learn        Hybrid + auto-save rules when confidence ≥ 0.90
      │
      ├─ Learned Rules  (DB-backed · source: human | ai_auto)
      ├─ Rule Classifier  (fast, deterministic — produces a hint, not a final answer)
      └─ LLM Classifier   (Qwen 2.5 · validates rule hint · extracts sender identity)
         │
         ├─ Rule matched + LLM agrees ──▶ source="rule_confirmed" · confidence ≥ 0.95
         │                                 └─▶ Actions (move_folder · export_pdf)
         │
         ├─ Rule matched + LLM disagrees ──▶ source="rule_conflict" · Telegram conflict card
         │                                    ⚠️ "Rule says Invoices · AI says Marketing"
         │                                    └─▶ You decide → move + optionally update rule
         │
         ├─ No rule + confidence ≥ 0.75 + Learning Mode OFF ──▶ Actions
         │
         ├─ No rule + confidence ≥ 0.75 + Learning Mode ON  ──▶ review-worker
         │                                                        └─▶ Telegram review card
         │                                                            ├─▶ Approve → move
         │                                                            ├─▶ Change folder → move + save rule?
         │                                                            └─▶ Fix sender → update identity
         │
         ├─ No rule + confidence < 0.75 ──▶ Telegram NeedsReview
         │                                   └─▶ You reply → move + learn rule
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
├── folders/                # Dynamic folder registry — DB-backed, drives LLM prompt + Telegram UI
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
├── invoices/               # Invoice QR extraction domain
│   ├── models.py           # Invoice ORM model (nif_seller, amounts, ATCUD, MB payment fields, raw_qr)
│   ├── qr_parser.py        # Portuguese AT/ATCUD QR string parser
│   └── extractor.py        # Calls AI Tool Server combined endpoint, deduplicates by seller+number, persists
├── telegram/               # Telegram bot — thin UI layer, pushes jobs to Redis
└── dashboard/              # Streamlit UI

alembic/                    # Database migration scripts
├── env.py                  # Async engine setup, all models imported
└── versions/
    ├── 001_baseline.py              # No-op — marks existing schema
    ├── 002_add_sender_fields.py     # Adds sender_name + sender_type to emails
    ├── 003_add_audit_logs.py        # Creates audit_logs table
    ├── 004_add_learned_rules_source.py  # Adds source column (human | ai_auto)
    ├── 005_add_folders.py               # Creates folders table, seeds defaults
    ├── 006_add_invoices.py              # Creates invoices table
    ├── 007_add_mb_payment_to_invoices.py    # Adds Multibanco payment columns to invoices
    ├── 008_invoice_dedup_by_seller_number.py  # Changes unique key from email_id to (nif_seller, invoice_number)
    ├── 009_learned_rules_conditions.py      # Adds conditions column to learned_rules
    ├── 010_add_companies.py                 # Creates companies table
    ├── 011_add_system_settings.py           # Creates system_settings table
    └── 012_add_invoice_document_type.py     # Adds document_type column (AT QR field D)

tests/
├── conftest.py             # Shared fixtures: FakeEmail, FakeSettings
└── unit/
    ├── test_crypto.py              # Encrypt/decrypt round-trips and error cases
    ├── test_rule_classifier.py     # Hardcoded + learned rule matching
    ├── test_hybrid_classifier.py   # Orchestration logic and threshold boundary
    ├── test_llm_classifier.py      # JSON parsing, normalisation, all error paths
    ├── test_operation_mode.py      # Mode get/set with fakeredis
    └── test_auto_save_rule.py      # Generic domain skip, human rule guard, new rule fields
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
| *(custom)* | Added via dashboard — immediately available to LLM and Telegram buttons |

Folders are stored in the `folders` DB table and managed from the **📁 Folders** dashboard page.
Renaming a folder updates all existing emails and renames the IMAP folder on every active account.
When the LLM suggests a folder name that doesn't exist yet, a Telegram card lets you create it with one tap.

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

# LLM — route through ai-api (recommended) or point directly at vLLM
# Using ai-api gives LangGraph tool routing + full message history support
LLM_BASE_URL=http://ai-api:8000/v1
LLM_API_KEY=your-api-api-key
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct-AWQ

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

# AI Tool Server — optional, enables PDF QR invoice extraction
TOOL_SERVER_URL=http://192.168.1.x:8000
TOOL_SERVER_API_KEY=your-tool-server-key

# Language — controls UI language for Telegram messages, dashboard, LLM prompts
# Supported: en (default), pt
LANGUAGE=pt
```

---

## Dashboard

After login, two pages are available from the sidebar:

**📊 Dashboard**

- Total emails processed, average AI confidence, average processing time
- Pie chart — classification distribution
- Bar chart — rule vs LLM decisions
- Audit table — last 200 processed emails with sender identity

**✉️ Email Accounts**

- List all configured accounts with active/inactive status
- Add IMAP account (password encrypted at rest with Fernet)
- Add Outlook / Microsoft 365 account
- Activate / deactivate / reset password

**📚 Learned Rules**

- View all learned rules with hit count, match condition, and action summary
- Enable / disable rules without deleting them
- Edit match field, match value, target folder, and PDF path inline
- Delete rules permanently
- Add rules manually without going through Telegram

**📁 Folders**

- List all classification folders with active/disabled status
- Add new folders — immediately available to the LLM prompt and Telegram buttons
- Rename folders — updates DB, all email records, and IMAP folder on every active account
- Enable / disable without deleting
- Delete folders — blocked if any emails still reference them

**🧾 Invoices**

- Automatically populated when PDF attachments on Invoices-classified emails are decoded via the AI Tool Server
- QR code data (AT/ATCUD): NIF seller/buyer, invoice number, date, taxable amount, VAT, gross total
- Payment data (extracted from PDF text via LLM): Multibanco entity, reference, amount, due date
- Deduplication: same invoice number + seller = one record, even if received in multiple emails
- KPI row: gross total, total VAT, taxable base, unique sellers
- Bar chart: top 10 sellers by gross amount
- Filterable table: by date range, NIF seller, invoice number / ATCUD code
- CSV export
- Delete: select one or multiple records with confirmation, removes only the invoice data (original email kept)

**📋 Audit Log**

- Filterable by actor name, action type, and date range
- Covers all system actions: AI classifications, human corrections, rule changes, account management, admin commands, DB migrations

---

## Development

**Run tests**

```bash
pip install -r requirements-dev.txt
pytest                  # all tests
pytest tests/unit/      # unit tests only
pytest -v               # verbose output
```

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

- [ ] Invoice OCR — handle scanned PDFs with no text layer
- [ ] Supplier name resolution — map NIF to company name via AT public registry
- [ ] Invoice status tracking — paid / unpaid / overdue based on due date
- [ ] REST API (FastAPI) for external integrations
- [ ] Webhook notifications on classification events
- [ ] Docker health check endpoints
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
- [x] Learned rules dashboard page — view, enable/disable, edit, delete, add manually
- [x] Operation Mode system — hybrid / rules_only / llm_only / auto_learn; switchable from dashboard or Redis
- [x] Auto-learn — high-confidence LLM decisions auto-saved as learned rules (source: ai_auto)
- [x] Unit test suite — crypto, classifiers, operation mode, auto-save logic; no external services needed
- [x] CI pipeline — tests gate every push; Portainer deploy only fires on master after green tests
- [x] Rule validation — LLM always confirms rule matches; rule_confirmed (agree) or rule_conflict (disagree → human)
- [x] Rule conflict card — Telegram shows exactly what rule said vs what AI said; human decides
- [x] Invoice MB payment extraction — LLM reads PDF text layer, extracts Multibanco entity/reference/amount/due date
- [x] Invoice deduplication — three-level: (nif_seller, invoice_number) → ATCUD → email_id fallback
- [x] Invoice document_type — AT QR field D stored; non-payment types skip payment extraction
- [x] ATCUD QR selection — picks first QR containing field H; decorative QR codes ignored
- [x] Invoices delete — dashboard allows selecting and removing invoice records with confirmation
- [x] LLM routing via ai-api — mailflow routes through ai-api /v1/chat/completions with full message history
- [x] Full i18n — dashboard, Telegram notifications, query worker; controlled via `LANGUAGE` env var
- [x] Dashboard login persistence — cookie-based; survives page refresh without re-login
- [x] Keyword extractor — preserves accented Portuguese chars, rejects codes and digit strings
- [x] LLM folder names — prompts enforce exact folder names, no English translation

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

Ricardo Grangeia — Software Engineer — Portugal
<https://ricardo.grangeia.pt>

---

## License

MIT License
