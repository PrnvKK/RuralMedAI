# Parchee Edge — Agent Handoff

## What this project is

A local-first medical scribe and claim-readiness assistant for low-connectivity rural clinics. It captures doctor-patient consultation audio via browser mic, sends it over WebSocket to a FastAPI backend, runs adaptive VAD, and passes speech windows to a local Gemma 4 multimodal model (via llama.cpp) that extracts structured clinical data. After review, the encounter is encrypted (AES-256-GCM) and committed to PostgreSQL. Background tasks run offline ICD-10-CM / ICD-10-PCS coding with TF-IDF + ChromaDB.

**Stack:** FastAPI (Python 3.14), Next.js 16 (TypeScript), PostgreSQL 16, llama.cpp server, Gemma 4 E2B GGUF (Q4_K_M), ChromaDB, sentence-transformers, scispacy.

---

## What's been done in this session

### Setup pipeline (`scripts/setup.py`)
One-command bootstrap that:
- Creates Python venv
- Installs all deps (requirements.txt + scispacy model)
- Downloads llama.cpp Windows CPU binaries from latest GitHub release
- Downloads Gemma 4 GGUF + mmproj via `huggingface_hub` (cached to `~/.cache/huggingface/hub/`)
- Generates AES-256 key, creates `.env` files with real paths
- Starts PostgreSQL via Docker

### `llama_server_manager.py` — upgraded
- Replaced raw `urllib.request` model downloads with `huggingface_hub.hf_hub_download()` (resume, progress, SHA verification)
- Added URL download as fallback for Docker path
- Better error messages directing users to run `python scripts/setup.py`

### `main.py` — non-blocking warmup
- ICD-10-CM/ICD-10-PCS embedding warmup moved to `asyncio.create_task()` (was `await`), so backend starts instantly instead of waiting 10-30 min for 150K+ embeddings to compute on CPU.

### Default port changed: 8080 → 8085
- Port 8080 was occupied by QScan on this machine. All defaults moved to 8085 across `.env.example`, `docker-compose.yml`, `llama_server_manager.py`, `llama_cpp_gemma_service.py`, `summarizer.py`, `README.md`.

### `requirements.txt` — Python 3.14 compat
- Bumped `spacy>=3.7` → `spacy>=3.8` (spaCy 3.7.x can't build on Python 3.14)
- Removed `scispacy` (installed separately with `--no-deps` because it pins `spacy<3.8`)
- Added `huggingface-hub>=0.26.0`, `tqdm>=4.66.0`

### scispacy model config fix
- `en_core_sci_md` 0.5.4 had `include_static_vectors = "True"` (string) and `normalize = "False"` (string) in `config.cfg`. SpaCy 3.8 requires booleans. Fixed in `.venv/Lib/site-packages/en_core_sci_md/en_core_sci_md-0.5.4/config.cfg`.

### `docker-compose.yml` — CPU default
- Default image changed from `server-cuda` to `server` (CPU). GPU image opt-in via env var.
- Ports updated to 8085.

### Other cleanup
- Deleted empty `backend/app/core/config.py`
- Deleted stray `backend/ruralmed.db`
- Updated `.gitignore` (`*.db`, `*.download`, `backend/data/`)

### Git
- Branch: `feature/final-polish`
- 2 commits: setup script + port fix

---

## Current state — everything is running BUT

**The backend starts, llama-server starts, audio chunks are processed, but Gemma 4 only generates 2 tokens per chunk.** This means the UI shows "Processing audio chunk 1..." but no fields populate.

A debug log line was added to `llama_cpp_gemma_service.py:331`:
```python
logger.info("Gemma response: finish_reason=%s, length=%d, raw=%r",
            finish_reason, len(content), content[:500])
```
**Next step:** restart the backend, record audio, and check this log line to see what the model actually returns. The `finish_reason` and `raw` content will diagnose whether it's a prompt format issue, thinking-block issue, chat template issue, or audio encoding issue.

### Possible causes of the 2-token problem:

1. **Chat template mismatch** (`backend/llama_templates/gemma4_no_think.jinja`) — The template renders `{{ message['content'] }}` which, for multimodal messages with content arrays, might produce garbage. The llama.cpp server *should* handle multimodal content internally before template rendering, but this needs verification.

2. **Gemma 4 thinking mode** — Even with `--reasoning off` and `reasoning_format: "none"`, the model might output `<think>...</think>` blocks that get stripped, leaving only 2 tokens of actual content. The `strip_thinking()` function removes these but if everything is in `<think>`, the result is empty.

3. **Audio not properly decoded** — The model might not be receiving/understanding the WAV audio, so it sees silence and returns empty JSON.

4. **`max_tokens` too low** — Default is 512, which should be plenty. Check the env var `LLAMA_CPP_MAX_TOKENS`.

5. **Prompt format** — `build_extraction_prompt()` generates a long prompt asking for JSON. The model might be confused and returning just `{}`.

### Debug checklist when you restart:
- [ ] `LLAMA_CPP_MAX_TOKENS` in `.env` — should be 512
- [ ] Look at the `Gemma response:` log line for `finish_reason` and `raw` content
- [ ] Check if the audio chunk has actual speech (not silence detection false positive)
- [ ] Try removing `chat_template_kwargs` and `reasoning_format` from the API call to see if defaults work better
- [ ] Try a text-only request to llama-server to verify the model works at all

---

## Remaining polish items (from original audit)

These were identified in the initial repo review but NOT addressed in this session:

### Critical
- [ ] **LICENSE** — No license file exists
- [ ] **CI/CD** — No GitHub Actions workflow
- [ ] **Tests** — Only 1 test file (15 unit tests). Zero frontend tests, zero integration tests.

### Security
- [ ] CORS allows all origins (`*`) in `backend/app/main.py:49`
- [ ] No API authentication on REST or WebSocket endpoints
- [ ] Hardcoded default DB credentials in `docker-compose.yml`
- [ ] `print()` used instead of `logging` in `database.py` (lines 127-128, 359, 368)

### Code quality
- [ ] Hardcoded `http://localhost:8003` in 13 places across frontend — needs central API config
- [ ] Heavy `any` type usage in TypeScript components
- [ ] `claimsEngine.ts` (~1014 lines) and `page.tsx` (~664 lines) are large single files
- [ ] `init_db()` called at module import time in `ehr.py:26` (side effect on import)
- [ ] Duplicated logic: `scheme_service.py` and `claimsEngine.ts` both implement insurance eligibility
- [ ] SQL via f-string for column names in `database.py:109`

### Missing professional polish
- [ ] `CONTRIBUTING.md`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`
- [ ] `.dockerignore`, `.editorconfig`, `.prettierrc`
- [ ] `pyproject.toml` or Python linting config (black/ruff/isort)
- [ ] Pre-commit hooks
- [ ] Database migrations (Alembic)
- [ ] Frontend `.env.example`
- [ ] Error boundaries or loading skeleton states in frontend
- [ ] `HF_HUB_DISABLE_SYMLINKS_WARNING=1` env var to suppress symlink warnings on Windows

---

## Key file locations

| File | Purpose |
|---|---|
| `scripts/setup.py` | One-command bootstrap script |
| `backend/app/main.py` | FastAPI app, websocket endpoint, lifespan |
| `backend/app/services/llama_server_manager.py` | Download models, start/stop llama-server |
| `backend/app/services/llama_cpp_gemma_service.py` | VAD, audio chunking, inference, JSON extraction |
| `backend/app/services/icd_coding_service.py` | ICD-10-CM 3-tier coding (TF-IDF + ChromaDB + scispacy) |
| `backend/app/services/procedure_coding_service.py` | ICD-10-PCS 3-tier coding |
| `backend/app/database.py` | PostgreSQL + AES encryption + CRUD |
| `backend/app/api/routes.py` | `/api/generate-note` endpoint |
| `backend/app/api/ehr.py` | EHR CRUD, FHIR export, billing, analytics |
| `backend/llama_templates/gemma4_no_think.jinja` | Custom Jinja chat template (no thinking mode) |
| `backend/.env` | Local config (AES key, model paths, ports) |
| `backend/.env.example` | Template for env file |
| `docker-compose.yml` | 5 services: postgres, gemma-models, llama-server, backend, frontend |
| `requirements.txt` | Python dependencies |
| `frontend/app/page.tsx` | Main scribe page (664 lines) |
| `frontend/hooks/useAudioStream.ts` | AudioWorklet mic capture |
| `frontend/hooks/useSocket.ts` | WebSocket connection to backend |
| `frontend/lib/claimsEngine.ts` | 9 insurance scheme rule engines (1014 lines) |
| `.venv/` | Python virtual environment (gitignored) |
| `backend/llama_cpp/bin/` | llama.cpp Windows binaries (gitignored) |
| `~/.cache/huggingface/hub/` | Cached GGUF models (~3.8 GB) |

---

## Startup commands

```powershell
# Terminal 1 — PostgreSQL
docker compose up -d postgres

# Terminal 2 — Backend (must be in backend/ directory)
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8003

# Terminal 3 — Frontend
cd frontend
npm run dev
```

Open `http://localhost:3000`.
