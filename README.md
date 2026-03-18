# MailFlow Engine

AI-powered email automation and classification engine.

MailFlow is part of the **Appa8 AI Process Automation Hub**, designed to process emails automatically, extract information, classify messages using AI, and trigger automated workflows.

The system is designed with a **modular worker architecture** and can run fully **on-premise**.

---

# Features

- IMAP email ingestion
- Email parsing (RFC822)
- Attachment storage
- AI classification
- Redis-based task queue
- PostgreSQL persistence
- Modular worker architecture
- Docker-based deployment

---

# Architecture

The system is composed of multiple independent workers:

```
Email Inbox
     │
     ▼
Email Worker
(fetch IMAP messages)
     │
     ▼
Parser
(email + attachments)
     │
     ▼
Redis Queue
     │
     ▼
AI Worker
(LLM classification)
     │
     ▼
Database + Storage
```

Components:

| Service | Description |
|-------|-------------|
| email-worker | Fetches emails from IMAP |
| api-worker | Handles API-triggered processing |
| ai-worker | Runs AI classification |
| Redis | Task queue |
| PostgreSQL | Persistence layer |
| Storage | Attachment and email storage |

---

# Tech Stack

Backend:

- Python
- FastAPI
- AsyncIO
- SQLAlchemy
- PostgreSQL
- Redis

AI:

- Local LLM support
- OpenAI-compatible APIs
- Qwen models

Infrastructure:

- Docker
- Docker Compose
- Linux

---

# Project Status

### Completed

- Worker architecture
- Docker-based development environment
- Check Imap Emails Accounts
- Add Email to Redis Queue
- AI Worker check Queue Send to LLM for AI classification
- In Imap Account Move Email to Folder related to classification

### In Progress

- Basic application layout
- Routing system
  - `/dashboard`
  - `/mail-accounts`
- Backend API integration

### Planned

- Invoice extraction
- Supplier detection
- Multi-tenant support
- Workflow automation
- Dashboard and analytics

---

# Installation

Clone the repository:

```bash
git clone https://github.com/yourusername/mailflow-engine.git
cd mailflow-engine
```

Create environment file:

```bash
cp .env.example .env
```

Edit `.env` with your configuration.

Start services:

```bash
docker compose up -d
```

---

# Environment Configuration

Example `.env.example`:

```
DATABASE_URL=postgresql+asyncpg://user:password@postgres:5432/mailaiworker
REDIS_URL=redis://redis:6379/0
STORAGE_ROOT=/storage
LLM_BASE_URL=http://fastapi:8000/v1
LLM_API_KEY=your-api-key
LLM_MODEL=qwen2.5-7b-instruct
```

---

# Development

Run workers locally:

```
python -m app.main
python -m app.api_worker
python -m app.ai_worker
```

---

# Future Vision

MailFlow is intended to become a **modular AI automation platform for business workflows**, including:

- email automation
- document processing
- invoice recognition
- supplier management
- internal knowledge assistants

The system is designed to support **on-premise deployments for companies that require full data privacy**.

---

# Author

Ricardo Grangeia  
Senior Software Engineer  
Portugal

Website  
<https://ricardo.grangeia.pt>

---

# License

MIT License
