# Second Brain

**An AI system that builds and maintains a structured model of what you know.**

Second Brain ingests your own material — PDFs, Markdown, web pages, YouTube transcripts,
plain text, DOCX, an Obsidian vault — extracts the concepts in it, discovers how they
relate, keeps that knowledge graph current, and answers questions from it with citations
back to the exact passage. It also tracks what *you* actually understand, separately from
what your library contains. Local-first, single-user, no recurring API costs required.

The design and its reasoning live in [ARCHITECTURE.md](ARCHITECTURE.md). Read that first.

> **Status:** Phase 2 (semantic search) complete — import PDFs, Markdown, text, DOCX, web
> pages and YouTube transcripts, file them under subjects and weeks, and search what they
> *say* by meaning and keyword, with every hit traced to its chunk. A local embedding model
> does the work; no API key, no LLM yet. See
> [Development phases](ARCHITECTURE.md#10--development-phases).

---

## Stack

| Layer | Choice | Why (short version — the long one is in ARCHITECTURE.md) |
| --- | --- | --- |
| Frontend | Next.js · TypeScript · Tailwind | A pure client for the API; no route handlers, no second backend. |
| Backend | Python · FastAPI | Typed request/response contracts, OpenAPI for free, Python for the parsing/ML ecosystem. |
| Worker | Plain Python process | Long ingestion jobs must survive API reloads. |
| Database | PostgreSQL 17 · pgvector · pg_trgm | Source text, embeddings, the graph and the job queue in **one** database, so every interesting query is one join. |
| Queue | PostgreSQL (`SKIP LOCKED`) | Correct, durable, inspectable with SQL, zero extra services. |
| Migrations | Alembic | The schema will change weekly for months. |
| Local infra | Native PostgreSQL service (Docker Compose optional) | Everything runs natively on the developer machine; no virtualisation required. |
| Embeddings | `BAAI/bge-small-en-v1.5` (384-d) on CPU via `fastembed` (ONNX Runtime) | Local and free: a one-time 67 MB download, no PyTorch, no service. Behind a provider protocol, so a bigger or hosted model is a config change (+ one migration if the width changes). |
| AI | Local models first, behind swappable provider protocols | No paid API required for the core application; upgradable later by configuration. |

Modular monolith, not microservices. No LangChain / LlamaIndex — the pipeline is explicit
and ours.

---

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.12+ | 3.13 is what this was built on. |
| Node.js | 20 LTS or newer | For the Next.js frontend. |
| PostgreSQL | 17 | Native install (below). pgvector is built from source once — needs the C++ build tools. |
| Visual Studio Build Tools 2022 | C++ workload | Only to compile pgvector. ~3–7 GB, free, one-time. |
| Git | any recent | |

Docker is **not** required. `docker-compose.yml` is kept as an alternative for machines
that have Docker — see [Alternative: Docker](#alternative-docker).

**Installing on Windows** (PowerShell, as your normal user — accept the UAC prompts):

```powershell
winget install OpenJS.NodeJS.LTS
winget install PostgreSQL.PostgreSQL.17 --override "--mode unattended --unattendedmodeui none --superpassword postgres --serverport 5432 --disable-components pgAdmin,stackbuilder"
winget install Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

`--superpassword` sets the `postgres` superuser password; pick your own if you like and
use it wherever `postgres` is typed below. Close and reopen every terminal (and VS Code)
afterwards so `node` and `npm` are on `PATH`. Verify:

```powershell
node --version
npm --version
Get-Service postgresql-x64-17          # Status: Running
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" --version
```

---

## Local setup

All commands are from the repository root unless stated.

> Quick start for the whole system, once steps 1–4 are done: PostgreSQL is already
> running as a service, so it is three terminals — `secondbrain-api`,
> `secondbrain-worker`, and `npm run dev` in `apps/web`.

### 1. Configuration

```powershell
Copy-Item .env.example .env
```

`.env` is gitignored and is the single source of configuration for the API, the worker,
the frontend and (if used) the Docker database. The defaults match `scripts/init-db.sql`.

### 2. Database (native PostgreSQL 17 + pgvector)

PostgreSQL runs as the Windows service `postgresql-x64-17` (auto-starts at boot; data in
`C:\Program Files\PostgreSQL\17\data`). Three one-time steps:

**a. Build and install pgvector** — the [official Windows procedure](https://github.com/pgvector/pgvector#windows).
Open **"x64 Native Tools Command Prompt for VS 2022" as Administrator** (Start menu; it is
installed by Build Tools) and run:

```bat
set "PGROOT=C:\Program Files\PostgreSQL\17"
cd %TEMP%
git clone --branch v0.8.6 https://github.com/pgvector/pgvector.git
cd pgvector
nmake /F Makefile.win
nmake /F Makefile.win install
```

It must be the **x64** prompt (not x86) and it must be elevated — `install` writes into
`Program Files`. No server restart is needed.

**b. Listen on loopback only** (local-first posture; the installer defaults to `*`):

```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h 127.0.0.1 -c "ALTER SYSTEM SET listen_addresses = 'localhost'"
Restart-Service postgresql-x64-17      # admin PowerShell
```

Authentication is already loopback-only in `pg_hba.conf`.

**c. Create the role and database:**

```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h 127.0.0.1 -f scripts\init-db.sql
```

Creates role `secondbrain` / password `secondbrain`, the database `secondbrain` (the
values in `.env.example`) plus `secondbrain_test` for the integration tests, and installs
the `vector` and `pg_trgm` extensions into both. Idempotent.
The extensions are created here, as superuser, because `vector` is not a *trusted*
extension and the app role is deliberately not a superuser; the Alembic migration's
`CREATE EXTENSION IF NOT EXISTS` then simply finds them already present.

To wipe everything and start over: `psql -U postgres -h 127.0.0.1 -c "DROP DATABASE secondbrain"`
and re-run step c.

#### Alternative: Docker

On a machine with Docker (requires hardware virtualisation + WSL 2 on Windows):
`docker compose up -d` runs `pgvector/pgvector:pg17` bound to `127.0.0.1:5432` with
pgvector preinstalled and the role/database created from the `POSTGRES_*` values in
`.env`. Skip steps a–c. The application cannot tell the two apart — it only reads
`DATABASE_URL`.

### 3. Backend

```powershell
cd apps/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Git Bash: source .venv/Scripts/activate
pip install -e ".[dev]"
alembic upgrade head                  # jobs, subjects, documents, chunks (revision 0002)
secondbrain-api                       # http://127.0.0.1:8000 — reloads on save
```

Interactive API docs: <http://127.0.0.1:8000/docs>. Health: <http://127.0.0.1:8000/v1/health>.

In a second terminal (same venv), the background worker:

```powershell
secondbrain-worker
```

It runs the ingestion stages (`ingest.parse` → `ingest.chunk` → `embed.chunks`) for every
import and idles otherwise. Keep it running while you import; a stopped worker simply
leaves documents `pending` (or unindexed) until it is started again. The first embedding
job downloads the model (~67 MB) into `data/models/`; after that it is fully offline.

### 4. Frontend

```powershell
cd apps/web
npm install
npm run dev                           # http://localhost:3000
```

The dashboard calls FastAPI **directly from the browser** at `NEXT_PUBLIC_API_URL`
(from the root `.env`; `next.config.ts` loads it) — there are no Next.js API routes.

**Typed API client.** `src/lib/api/schema.d.ts` is generated from the backend's OpenAPI
schema. After changing any route or Pydantic schema:

```powershell
cd apps/api;  secondbrain-openapi        # writes apps/api/openapi.json
cd ../web;    npm run generate:api       # regenerates src/lib/api/schema.d.ts
```

Commit both. A backend test fails if `openapi.json` is stale, and the frontend
typecheck fails if the client no longer matches — so a contract change cannot slip
through silently.

---

## Using it

Open <http://localhost:3000/library>. Choose a local file (`.pdf`, `.md`, `.txt`,
`.docx`) or paste a web page / YouTube URL, type one or more subjects (comma-separated;
unknown names are created), optionally a week, and press **Import**. The request returns
immediately; the table polls until the worker has parsed and chunked the document. The
**Find document by title** box narrows the table by title or original filename/URL
(case-insensitive, combinable with the subject/week/status filters — it is a lookup, not
content search). The **Indexed** column shows how many of a document's chunks have an
embedding for the current model. Click a title to see its metadata, its job history and
every chunk with its heading path, character offsets, page numbers and token estimate.
**Reprocess** re-parses (and re-embeds) from the managed copy; **Delete** removes the
document, its chunks, its vectors and its managed copy.

### Two different searches

| | Where | What it matches | How |
| --- | --- | --- | --- |
| **Find document by title** | Library | Document titles / original filenames | case-insensitive substring (`ILIKE`) |
| **Search Knowledge** | <http://localhost:3000/search> | What the chunks *say* | embeddings (meaning) + PostgreSQL full-text (keywords), fused |

On the Search Knowledge page type a question in your own words — *"How does a neural
network learn?"* — and pick a mode: **hybrid** (default; meaning and keywords fused with
reciprocal rank fusion), **semantic** (meaning only) or **keyword** (exact words only,
all of them must appear). Each result shows the source document, heading path, page and
character range, the chunk text, and its scores. The footer shows index coverage; **Index
now** queues embedding jobs for anything not yet indexed (safe to press repeatedly — it
never duplicates work). Nothing here generates an answer; that is Phase 3.

What happens to an import (ARCHITECTURE.md §6):

1. **Register** (in the request): the bytes are hashed (files) or the URL is hashed
   (web/YouTube); a known hash returns the existing document — with any new subjects
   added — instead of a copy. Files are copied to `data/originals/<hash[:2]>/<hash>.<ext>`
   and never read from their original location again.
2. **Parse** (worker): PyMuPDF for PDF (headings from font size), python-docx for DOCX
   (heading styles), trafilatura for web pages, `youtube-transcript-api` for videos,
   the standard library for Markdown and text. Every parser yields the same shape:
   normalised text plus heading/page/timestamp offsets into it.
3. **Chunk** (worker): one *section* chunk per heading (with the full heading path) and
   ~300-token *retrieval* chunks inside it, 12 % overlap, exact `char_start`/`char_end`
   into `documents.raw_text`. A reprocess supersedes the old chunks rather than deleting
   them.
4. **Embed** (worker): every retrieval chunk without a vector for the configured model is
   embedded in batches and stored in `chunk_embeddings` (pgvector, HNSW cosine index),
   keyed by chunk *and* model so models can coexist and a switch is a re-index. Already
   embedded chunks are skipped; `POST /v1/embeddings/index {"force": true}` regenerates.

Not supported yet: scanned PDFs without a text layer (rejected with a clear error — OCR
is out of scope), videos without transcripts, pages behind logins.

The same operations are available on the API (<http://127.0.0.1:8000/docs>):
`POST /v1/documents` (multipart: `file` *or* `url`, `subjects`, `week`, `title`),
`GET /v1/documents?q=&subject_id=&week=&status=&kind=`, `GET /v1/documents/{id}`,
`GET /v1/documents/{id}/chunks?level=all|sections|retrieval`, `DELETE /v1/documents/{id}`,
`POST /v1/documents/{id}/reprocess`, `GET /v1/subjects`, `POST /v1/subjects`,
`GET /v1/jobs/{id}`, `GET /v1/search?q=&mode=hybrid|semantic|keyword&limit=&subject_id=&week=&kind=&document_id=`,
`GET /v1/embeddings/status`, `POST /v1/embeddings/index`.

---

## Verifying Phases 0–2

| Check | Command | Expect |
| --- | --- | --- |
| Backend lint | `cd apps/api; ruff check .; ruff format --check .` | `All checks passed!` |
| Backend tests | `cd apps/api; pytest` | 107 tests pass (one real-model test skips until the model has been downloaded). Integration tests (marked `integration`) run against the **`secondbrain_test`** database (created by `scripts/init-db.sql`, migrated automatically) so they never touch your library or race the running worker; they **skip** if it is unreachable. |
| Migration SQL (no DB needed) | `cd apps/api; alembic upgrade head --sql` | The SQL for `jobs`, `subjects`, `documents`, `document_subjects`, `chunks` printed to stdout. |
| Models match migrations | `cd apps/api; alembic check` | `No new upgrade operations detected.` |
| Database up | `Get-Service postgresql-x64-17` | `Running` (Docker alternative: `docker compose ps` → `healthy`) |
| pgvector installed | `psql -U secondbrain -h 127.0.0.1 -d secondbrain -c "select extversion from pg_extension where extname='vector'"` | `0.8.6` (installed by `scripts/init-db.sql`) |
| Migrated | `cd apps/api; alembic current` | `0003 (head)` |
| API up | `curl http://127.0.0.1:8000/v1/health` | `"status":"ok"` with `pgvector_version` and `migration_revision` populated. `"degraded"` means the DB is unreachable or unmigrated — the `database.error` field says which. |
| Worker up | `secondbrain-worker` | `worker … online; handles: embed.chunks, ingest.chunk, ingest.parse, system.ping`. |
| Frontend checks | `cd apps/web; npm run typecheck; npm run lint; npm run build` | All clean. |
| Frontend ↔ backend ↔ DB | open <http://localhost:3000> | The **System health** card shows the API version, PostgreSQL version, pgvector version and migration `0003`, all green. If PostgreSQL is down the card says so — that is still the frontend talking to the backend; fix the DB and press Refresh. |
| Ingestion end to end | <http://localhost:3000/library>: import a PDF with a subject and week | Status goes `pending` → `ready` within seconds (worker running); the document page lists section chunks whose heading paths match the PDF's headings and retrieval chunks with exact character offsets into the stored text. |
| Index coverage | `curl http://127.0.0.1:8000/v1/embeddings/status` | `chunks_embedded == chunks_total` once the worker has run; otherwise press **Index now** on the Search page (or `curl -X POST …/v1/embeddings/index`). |
| Knowledge search | <http://localhost:3000/search>: ask *"How does a neural network learn?"* | Chunks from the relevant sources, each with document, heading path, page/character location and score; **semantic** and **keyword** modes return different rankings; the Library's title box is unaffected. |

---

## Project structure

```text
second-brain/
├─ ARCHITECTURE.md            # the design, with every decision and its alternative
├─ README.md
├─ docker-compose.yml         # OPTIONAL containerised PostgreSQL, for machines with Docker
├─ scripts/init-db.sql        # one-time role + database bootstrap for native PostgreSQL
├─ .env.example               # copy to .env (gitignored)
├─ data/originals/            # gitignored — managed copies of imported files, by content hash
├─ data/models/               # gitignored — the downloaded embedding model
└─ apps/
   ├─ api/                    # Python backend: FastAPI app + worker, one package
   │  ├─ pyproject.toml       # deps, scripts (secondbrain-api, secondbrain-worker), ruff, pytest
   │  ├─ alembic.ini
   │  ├─ migrations/          # Alembic env + versions/ (0001 jobs; 0002 subjects/documents/chunks)
   │  ├─ tests/
   │  └─ src/secondbrain/
   │     ├─ config.py         # pydantic-settings; the environment is the only config source
   │     ├─ main.py           # FastAPI app factory; `secondbrain-api` entrypoint
   │     ├─ worker.py         # queue drain loop; `secondbrain-worker` entrypoint
   │     ├─ openapi.py        # exports openapi.json; `secondbrain-openapi` entrypoint
   │     ├─ api/v1/           # routers (thin): health, documents, subjects, jobs
   │     ├─ schemas/          # Pydantic HTTP contracts
   │     ├─ services/         # documents.py, subjects.py, storage.py, embeddings.py, search.py
   │     ├─ pipeline/         # parse/ (one parser per kind → ParsedDocument), chunk.py, stages.py
   │     ├─ db/               # base.py (naming convention), engine.py, models/
   │     ├─ queue/            # queue.py (SKIP LOCKED), registry.py (job type → handler + on_failure)
   │     └─ providers/        # llm/base.py (protocol only); embedding/ (protocol, fastembed, fake)
   └─ web/                    # Next.js 16 · TypeScript · Tailwind 4 · App Router
      ├─ next.config.ts       # loads the root .env; exposes NEXT_PUBLIC_API_URL
      └─ src/
         ├─ app/              # layout.tsx, page.tsx (dashboard), library/ (list + [id]), search/
         ├─ components/       # HealthCard, ImportForm, Library, DocumentView, KnowledgeSearch, …
         └─ lib/api/          # client.ts (openapi-fetch) + schema.d.ts (generated)
```

---

## Conventions

- **Configuration** only ever enters through `Settings` (`config.py`). No `os.environ`
  elsewhere.
- **Routers are thin.** They validate input, call a service, return a schema.
- **Handlers don't commit.** A job handler writes through the session it is given; the
  worker commits its output together with the job's completion, or rolls both back.
- **Migrations are reviewed, not just autogenerated.** `alembic revision --autogenerate`
  is a starting point; check the file before applying it.
- **Nothing from a later phase is built early.** If it isn't in the current phase, it
  waits.
