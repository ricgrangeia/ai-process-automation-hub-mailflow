# MailFlow Engine

> Version 2.8.0 — Part of the [Appa8 AI Process Automation Hub](https://appa8.com)

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
| Email Accounts UI — add / toggle / reset password / worker mode | ✅ |
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
| Dashboard Learned Rules page — search/filter by keyword, folder, status; edit, delete, add | ✅ |
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
| Invoice QR extraction — PDF attachments decoded via AI Tool Server | ✅ |
| Invoice payment data — Multibanco entity, reference, amount and due date extracted via LLM | ✅ |
| Invoice deduplication — (nif_seller, invoice_number) → ATCUD → email_id, three-level dedup | ✅ |
| Invoice document_type — AT field D parsed and stored (FT, FS, FR, ND, NC, GR, GT…) | ✅ |
| Invoice document_type_description — human-readable label derived from document_type code | ✅ |
| Payment extraction gate — payment step skipped for non-payment doc types (ND, NC, GR, etc.) | ✅ |
| ATCUD QR selection — picks first QR with field H; skips decorative/tracking QR codes | ✅ |
| International invoice support — non-PT-AT PDFs extracted via LLM (seller_name, country, currency, VAT rate…) | ✅ |
| Invoice origin tracking — pt_at / international / payment_confirmation / bank_transfer / receipt | ✅ |
| Invoice dashboard — two tabs: 🇵🇹 AT Invoices + 🌍 Foreign; KPIs, charts, CSV export, delete | ✅ |
| Invoice Worker — dedicated service for invoice-only managed accounts | ✅ |
| Per-account worker mode — ai_worker (full AI) or invoice_worker (invoices only) | ✅ |
| Invoice Worker body detection — keyword scan + LLM extraction for emails without PDF | ✅ |
| Payment method field — card / bank_transfer / mbway / multibanco / paypal | ✅ |
| LLM routing via AI API — mailflow routes LLM calls through ai-api instead of vLLM directly | ✅ |
| Full i18n — dashboard, Telegram notifications, query worker; `LANGUAGE=pt` env var | ✅ |
| Dashboard login persistence — cookie-based session, survives page refresh without re-login | ✅ |
| Keyword extractor — real words only: preserves accented chars, rejects codes and digit strings | ✅ |
| LLM folder names — prompts enforce exact folder names, no translation to English | ✅ |
| Dashboard flash feedback — all save/delete actions show success or error after rerun | ✅ |
| Unified IMAP worker routing — single worker polls all accounts, routes to jobs:email or jobs:invoice by managed_by | ✅ |
| Invoice Worker as Redis consumer — BRPOP on jobs:invoice; no direct IMAP polling | ✅ |
| Marketing email filter — is_marketing() blocks newsletters before keyword scan (List-Unsubscribe / unsubscribe in body) | ✅ |
| NIF / seller name lookup — ai-api tool endpoint; DDG-first strategy; nif.pt for metadata; in-process cache | ✅ |
| Sellers table — DB cache of resolved NIF → company name; backfills invoices.seller_name automatically | ✅ |
| 🏪 Sellers dashboard page — paginated data_editor with inline delete, search, manual NIF lookup, CSV export | ✅ |
| Invoices dashboard account filter — filter KPIs, charts, and tables by email account | ✅ |
| Invoices dashboard monthly totals chart — bar chart of gross total per month | ✅ |

---

## Architecture

```text
IMAP / Outlook
      │
      ▼
 email-worker / api-worker   ←── single IMAP worker polls ALL accounts
 parse RFC822 · route by managed_by
      │
      ├─ managed_by = ai_worker  ──────────▶  Redis  mailai:jobs:email
      │                                             │
      │                                             ▼
      │                                          ai-worker  [Operation Mode]
      │
      └─ managed_by = invoice_worker
           │
           ├─ is_marketing()?  YES → leave untouched (unread, unmoved)
           ├─ has PDF?          YES → store + enqueue
           └─ financial keywords? YES → store + enqueue
                 │
                 ▼
            Redis  mailai:jobs:invoice
                 │
                 ▼
            invoice-worker  (BRPOP consumer)
            ├─ PDF → AI Tool Server → QR/LLM extract
            │        → save DB + archive PDF + move + Telegram notify
            └─ body keywords → LLM body extract
                     → save DB + move + Telegram notify
            (both paths) → NIF lookup → sellers table → seller_name backfill

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
| `email-worker` | Polls **all** IMAP accounts; routes to `jobs:email` (ai_worker) or `jobs:invoice` (invoice_worker) based on `managed_by`; pre-filters non-financial emails for invoice accounts |
| `api-worker` | Polls Microsoft Graph accounts (`managed_by = ai_worker`), enqueues jobs to `jobs:email` |
| `ai-worker` | Classifies emails, executes actions, runs DB migrations on startup |
| `invoice-worker` | **Redis consumer** (`BRPOP jobs:invoice`); loads email by ID from DB; PDF extract via tool server + body keyword detect; saves invoices + archives PDFs + moves emails + Telegram notify |
| `telegram-bot` | Thin UI layer — user input, NeedsReview callbacks, admin commands |
| `review-worker` | Learning Mode — sends review cards, handles Approve / Change / Fix sender |
| `query-worker` | Natural language search — LLM parse → DB search → deliver results |
| `dashboard` | Streamlit UI — supervision + account management (port 8501, Traefik-proxied) |
| `redis` | Job queues (AOF persistent) — `jobs:email`, `jobs:query`, `jobs:review` |
| `postgres` | Persistence (external, via `database-network`) |

### Invoice Worker — Collaborative Mode

The `invoice-worker` lets you use one mailbox for both human email and automated invoice management:

- Set `managed_by = invoice_worker` on any account from the dashboard
- The IMAP worker (email-worker) handles all accounts; for `invoice_worker` accounts it pre-screens emails before storing and routes the job to `mailai:jobs:invoice`
- The invoice-worker is a **Redis consumer** (BRPOP on `mailai:jobs:invoice`) — no IMAP polling; loads the stored email from DB by `email_id`
- **Marketing gate** — `is_marketing_email()` checks `List-Unsubscribe` header and body for unsubscribe links before any keyword matching; newsletters and promotional emails are left completely untouched (unread, unmoved), even if they contain words like "payment"
- **PDF emails** → tool server QR decode → LLM merge → save DB → archive PDF → move to `Invoices` folder → Telegram notification
- **Body-only financial emails** (payment confirmations, bank transfers, receipts) → keyword scan (free, zero LLM) → if matched: LLM body extraction → save DB → move
- **NIF lookup** — after saving, calls `ai-api /tools/nif/lookup` to resolve seller name; upserts into `sellers` table; backfills `invoices.seller_name`
- **Everything else** → left completely untouched (unread, unmoved)

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
│   ├── imap/               # IMAP client + polling worker (filters managed_by = ai_worker)
│   └── outlook/            # Microsoft Graph client + polling worker (filters managed_by = ai_worker)
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
├── invoices/               # Invoice extraction domain
│   ├── models.py           # Invoice ORM model — nif_seller/buyer, amounts, ATCUD, MB payment,
│   │                       #   document_type, document_type_description, invoice_origin,
│   │                       #   seller_name, seller_country, currency, vat_rate, receipt_number,
│   │                       #   card_last4, payment_method, raw_qr
│   ├── document_types.py   # DOCUMENT_TYPES dict — AT code → human label (FT→"Fatura", etc.)
│   ├── nif_lookup.py       # resolve_seller_name() — calls ai-api, upserts sellers, backfills invoices
│   ├── qr_parser.py        # Portuguese AT/ATCUD QR string parser
│   └── extractor.py        # Calls AI Tool Server, deduplicates by seller+number, persists
├── sellers/                # Sellers domain — NIF → company name cache
│   └── models.py           # Seller ORM model (nif, name, activity, cae, address, situation)
├── invoice_worker/         # Invoice Worker — collaborative mode for financial-only accounts
│   ├── detector.py         # Three-stage: PDF check → is_marketing() gate → keyword scan
│   ├── body_extractor.py   # LLM extraction from email body (payment_confirmation, bank_transfer…)
│   └── worker.py           # Redis consumer (BRPOP jobs:invoice); loads email from DB by ID
├── telegram/               # Telegram bot — thin UI layer, pushes jobs to Redis
└── dashboard/              # Streamlit UI
    └── app.py              # All pages: dashboard, accounts, rules, folders, companies, sellers,
                            #   invoices, settings, audit log; flash feedback, i18n, cookie login

alembic/                    # Database migration scripts (auto-run on ai-worker startup)
├── env.py                  # Async engine setup, all models imported
└── versions/
    ├── 001_baseline.py                          # No-op — marks existing schema
    ├── 002_add_sender_fields.py                 # sender_name + sender_type on emails
    ├── 003_add_audit_logs.py                    # audit_logs table
    ├── 004_add_learned_rules_source.py          # source column (human | ai_auto)
    ├── 005_add_folders.py                       # folders table + defaults
    ├── 006_add_invoices.py                      # invoices table
    ├── 007_add_mb_payment_to_invoices.py        # Multibanco payment columns
    ├── 008_invoice_dedup_by_seller_number.py    # unique key (nif_seller, invoice_number)
    ├── 009_learned_rules_conditions.py          # conditions jsonb column on learned_rules
    ├── 010_add_companies.py                     # companies table
    ├── 011_add_system_settings.py               # system_settings table
    ├── 012_add_invoice_document_type.py         # document_type (AT QR field D)
    ├── 013_add_invoice_document_type_description.py  # document_type_description String(60)
    ├── 014_add_invoice_international_fields.py  # invoice_origin, seller_country, currency,
    │                                            #   vat_rate, receipt_number, card_last4,
    │                                            #   payment_method
    ├── 015_add_managed_by_to_email_accounts.py  # managed_by on email_accounts (ai_worker | invoice_worker)
    └── 016_add_sellers_table.py                 # sellers table with unique index on nif

locales/
├── en/                     # English (default fallback)
│   ├── ui.toml             # Dashboard + Telegram UI strings
│   ├── prompt.classifier.system.txt    # LLM classifier system prompt
│   ├── prompt.classifier.user.txt      # LLM classifier user template
│   ├── prompt.classifier.rule_hint.txt # Rule hint formatting
│   ├── prompt.invoice.body.txt         # Invoice body extraction prompt
│   └── prompt.query.parser.txt         # NL query parser prompt
└── pt/                     # Portuguese (same file set; missing keys fall back to en)

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

| To add… | You only touch… |
|---|---|
| New email source (e.g. Gmail) | `ingestion/gmail/` |
| New classifier | `classification/` |
| New processing action (webhook, forward, ticket) | `processing/actions/` |
| New query output (Slack, webhook, PDF report) | `query/exporter.py` |
| New dashboard page | `dashboard/app.py` |
| New account type | `accounts/` |
| New invoice origin / body type | `invoice_worker/detector.py` + `locales/*/prompt.invoice.body.txt` |

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

## Invoice Origins

The `invoice_origin` field classifies the type of financial document found:

| Value | Source | How detected |
|---|---|---|
| `pt_at` | Portuguese AT invoice with ATCUD | QR code field H present |
| `international` | Foreign invoice without ATCUD | PDF with no ATCUD found → LLM extraction |
| `payment_confirmation` | Card / PayPal / MBWay confirmation | Email body keyword → LLM extraction |
| `bank_transfer` | Bank transfer statement | Email body keyword → LLM extraction |
| `receipt` | Store / online receipt | Email body keyword → LLM extraction |

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
LLM_API_KEY=your-api-key
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

# File archive root (for PDF export action and invoice-worker archiving)
FILES_ROOT=/files
COMPANY_NAME=MyCompany

# Language — controls UI language for Telegram messages, dashboard, LLM prompts
# Supported: en (default), pt
LANGUAGE=pt
```

---

## Dashboard

After login (cookie-persisted across page refreshes), the following pages are available:

### 📊 Dashboard

- Total emails processed, average AI confidence, average vLLM processing time
- Pie chart — classification distribution across folders
- Bar chart — rule vs LLM decisions (source breakdown)
- Table — last 200 processed emails with subject, sender identity, confidence, source, time

### ✉️ Email Accounts

- List all configured accounts with active/inactive status and assigned worker mode
- Per-account **Settings** expander:
  - **Worker Mode** — toggle between `🤖 Full AI Worker` and `🧾 Invoice Worker`; change takes effect immediately on next poll cycle
  - **Password reset** — update IMAP credential (re-encrypted with Fernet at rest)
- Add IMAP account (host, port, username, password encrypted at rest)
- Add Outlook / Microsoft 365 account (Graph UPN)
- Activate / deactivate accounts without deleting them

### 📚 Learned Rules

- **Filter bar** — search by keyword/sender value, filter by target folder, filter by Active/Disabled
- Live counter: `Showing N / total — M active · K disabled`
- Per-rule card: conditions, hit count, tenant, created date, active status
- Enable / disable individual rules without deleting
- **Edit** expander — change conditions, min-match threshold, target folder, PDF export path
- **Delete** button (outside form) — with success confirmation flash
- **Add Rule Manually** form — create rules without going through Telegram

### 📁 Folders

- List all classification folders with active/disabled status and creation date
- **Add** — creates in DB and on every active IMAP account simultaneously
- **Rename** — updates DB, all email records (`classification_label`), and IMAP folder on every active account
- **Enable / Disable** — without deleting
- **Delete** — blocked if any emails still reference the folder; removes from DB and IMAP

### 🏢 Companies

- Match companies to invoices via NIF (buyer/seller) for PDF archive routing
- Add / edit / delete companies with Name, NIF, Notes, Active flag
- Used by `ExportPdfAction` to route PDFs under the correct company folder

### 🏪 Sellers

- Resolved invoice senders — automatically populated by NIF lookup during invoice processing
- Paginated table with inline `🗑️` delete, search by NIF / name / activity, CSV export
- **Manual lookup** expander — enter any 9-digit NIF to trigger lookup and cache result immediately
- Data sourced from `ai-api /tools/nif/lookup` (DuckDuckGo + nif.pt); stored in `sellers` table
- Seller names backfill `invoices.seller_name` automatically on resolution

### 🧾 Invoices

Automatically populated when PDF attachments are decoded or financial emails are processed by the invoice-worker.

**🇵🇹 AT Invoices tab**

- QR code data (AT/ATCUD): NIF seller/buyer, seller name, invoice number, ATCUD, date, taxable amount, VAT, gross total
- Document type (AT field D) with human-readable description (Fatura, Fatura Simplificada, Recibo…)
- Payment data: Multibanco entity, reference, amount, due date
- KPIs: gross total, total VAT, taxable base, unique sellers
- Bar chart: monthly totals (gross per month)
- Bar chart: top 5 sellers by total (NIF + name labels)
- Filter: email account, last N months, NIF seller contains, invoice # / ATCUD contains

**🌍 Foreign tab**

- International invoices: seller name, country, currency, VAT rate, payment method, card last 4 digits
- Invoices extracted from non-PT-AT PDFs and body-based financial emails

**Common features**

- CSV export per tab
- Delete: select one or multiple records with confirmation — removes invoice data only (original email kept)

### ⚙️ Settings

- **Archive Folder Structure** — customise the path template for PDF export:
  `{company}/{year}/{month}-{month_name}/{category}/{supplier}`
- Live preview with example values
- Save / Reset to default

### 📋 Audit Log

- Filterable by actor name, action type, and date range
- Covers: AI classifications, human corrections, rule changes, account management, admin commands, DB migrations

---

## Telegram Commands

| Command | Description |
|---|---|
| `/start` | Show help menu |
| `/status` | Current operation mode, LLM model, inbox folder, learning mode status |
| `/learn on` | Activate Learning Mode — every LLM decision goes to human review before applying |
| `/learn off` | Deactivate Learning Mode — run autonomously |
| `/recover` | Re-queue all `needs_review` emails stuck in the pipeline |
| `/restart` | Gracefully restart the ai-worker |
| Any free text | Natural language query — "show me invoices from EDP last month" |

---

## Development

**Run tests**

```bash
pip install -r requirements-dev.txt
pytest                  # all tests
pytest tests/unit/      # unit tests only
pytest -v               # verbose output
```

**Run workers individually**

```bash
python -m app.ingestion.imap.worker      # email-worker (IMAP)
python -m app.ingestion.outlook.worker   # api-worker (Outlook)
python -m app.processing.worker          # ai-worker (classification + migrations)
python -m app.invoice_worker.worker      # invoice-worker (financial-only accounts)
python -m app.review.worker              # review-worker (Learning Mode review cards)
python -m app.telegram.bot               # telegram-bot (callbacks + admin commands)
python -m app.query.worker               # query-worker (NL search jobs)
streamlit run app/dashboard/app.py       # dashboard
```

**Local Docker Desktop (Windows dev)**

```bash
docker compose -f docker-compose.local.yml up -d
```

Uses `env_file: .env` — production reads vars from Portainer (no env_file).

**Useful Makefile commands**

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
- [ ] Invoice status tracking — paid / unpaid / overdue based on due date
- [ ] REST API (FastAPI) for external integrations
- [ ] Webhook notifications on classification events
- [ ] Docker health check endpoints
- [ ] PostgreSQL full-text search (GIN index on subject + body)
- [x] Invoice Worker — dedicated service for invoice-only managed accounts (managed_by column)
- [x] International invoice support — non-PT-AT PDFs extracted via LLM
- [x] Body-based financial detection — keyword scan + LLM for emails without PDF
- [x] Invoice origin tracking — pt_at / international / payment_confirmation / bank_transfer / receipt
- [x] Invoice dashboard two-tab layout — 🇵🇹 AT + 🌍 Foreign with separate columns and CSV
- [x] document_type_description — human-readable AT document type label
- [x] payment_method field — card / bank_transfer / mbway / multibanco / paypal
- [x] Rules page search filter — by keyword/sender, folder, active/disabled status
- [x] Dashboard flash feedback — all save/delete actions survive st.rerun() via session_state
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
- [x] Unified IMAP worker routing — single worker polls all accounts, routes by managed_by to two queues
- [x] Invoice worker as Redis consumer — BRPOP on jobs:invoice; no direct IMAP polling
- [x] Marketing email filter — is_marketing() blocks newsletters/promos before keyword detection
- [x] NIF / seller name lookup — ai-api tool; DDG-first + nif.pt enrichment; sellers table cache
- [x] Sellers dashboard page — paginated data_editor with inline delete, search, CSV export
- [x] Invoices account filter — KPIs and charts scoped per email account
- [x] Invoices monthly totals chart — gross total per month bar chart

See [CHANGELOG.md](CHANGELOG.md) for the full history.

---

## Tech Stack

- **Python 3.12** — AsyncIO, SQLAlchemy 2.0, httpx, tenacity
- **PostgreSQL** — asyncpg (workers) + psycopg2 (dashboard)
- **Redis** — job queue (AOF persistent)
- **Streamlit** + Plotly + Pandas — dashboard
- **Cryptography (Fernet)** — credential encryption at rest
- **Docker Compose** + Traefik — deployment
- **GitHub Actions** + Portainer — CI/CD
- **Alembic** — schema migrations (auto-applied on ai-worker startup)

---

## Author

Ricardo Grangeia — Software Engineer — Portugal  
<https://ricardo.grangeia.pt>

---

## License

MIT License
