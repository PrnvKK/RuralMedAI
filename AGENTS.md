# Parchee Edge — Agent Handoff

## What this project is

A medical scribe and claim-readiness assistant for low-connectivity rural clinics. It captures doctor-patient consultation audio via browser mic, sends it over WebSocket to a FastAPI backend, runs adaptive VAD, and passes speech windows to **Gemini 3.5 Transcribe** (speech-to-text) followed by **Gemini 3.5 Flash** (structured clinical extraction) via the Gemini Interactions API. After review, the encounter is encrypted (AES-256-GCM) and committed to PostgreSQL. Background tasks run offline ICD-10-CM / ICD-10-PCS coding with TF-IDF + ChromaDB.

**Stack:** FastAPI (Python 3.14), Next.js 16 (TypeScript), PostgreSQL 16, Gemini API (`gemini-3.5-transcribe` + `gemini-3.5-flash`), ChromaDB, sentence-transformers, scispacy.

**Branch:** `feature/gemini-3-5-transcribe` (branched from `feature/final-polish`)

---

## What's been done in this session

### Gemma → Gemini migration (complete)

The previous local Gemma 4 / llama.cpp pipeline was replaced end-to-end with the Gemini API. Announced model: https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5-transcribe/

**New files:**

- `backend/app/services/gemini_client.py` — thin REST client for the Interactions API (`POST https://generativelanguage.googleapis.com/v1beta/interactions`, `x-goog-api-key` header). Retries with backoff on 429/5xx/network errors. Raises on `status: "incomplete"` so truncated output is never parsed. Loads `.env` from repo root AND `backend/` (fixes running uvicorn from `backend/` while the key lives in root `.env`).
- `backend/app/services/gemini_transcribe_service.py` — replaces `llama_cpp_gemma_service.py`. Same VAD + same WebSocket message protocol (`content`, `update`, `session_complete`, `heartbeat`), so the frontend needed zero changes. Two-stage pipeline per speech window: `gemini-3.5-transcribe` → transcript, then `gemini-3.5-flash` with JSON `response_format` → clinical field updates.
- `backend/tests/test_gemini_transcribe_service.py` — 31 unit tests (parsing, validation, merging, VAD, client payload/retry/status handling, full pipeline with mocked client).
- `scripts/validate_gemini.py` — live validation tool: API key check, text smoke test, transcription of a WAV, extraction, full service pipeline. Run: `python scripts/validate_gemini.py audio.wav`.

**Deleted:** `llama_cpp_gemma_service.py`, `llama_server_manager.py`, `llama_templates/gemma4_no_think.jinja`, old test file.

**Updated:** `main.py` (no more llama-server lifecycle), `summarizer.py` (Gemini for summaries/notes), `routes.py` docstring, `docker-compose.yml` (removed gemma-models + llama-server services, passes `GEMINI_API_KEY` to backend), `scripts/setup.py` (no model downloads; prompts for/validates Gemini key), README, architecture.md, both `.env.example` files, frontend comment.

### Critical lessons learned (live-tested against the real API)

1. **Gemini 3.5 Flash is a thinking model.** Thought tokens count against `max_output_tokens`. With a 1024 cap, thinking consumed the whole budget → `status: "incomplete"` → truncated JSON. Fix: `GEMINI_MAX_OUTPUT_TOKENS=4096` + `thinking_level: "low"` in generation_config. (`thinking_config`/`thinking_budget` is NOT a valid Interactions API param — 400 error.)
2. **`response_format` (JSON schema) works** in the Interactions API and returns compact, valid JSON — used for the extraction call.
3. **Never block the WebSocket receive loop on inference.** A ~15s Gemini call while awaiting `receive_text()` stalls keepalive ping/pong handling → connection dies with 1011 "keepalive ping timeout". Fix: chunks are processed by an ordered background `asyncio.Queue` worker; the receive loop only enqueues. `end_session` drains the queue before `session_complete`.
4. The transcribe model accepts optional text instruction; we send "Transcribe this audio." plus optional custom vocabulary / language hints.

### Validation performed (all passing)

- 31 unit tests green (`python -m unittest discover -s tests` from `backend/`)
- `ruff check` + `ruff format --check` clean on all touched files
- Live API validation script: all 4 steps pass
- Full end-to-end WebSocket test against a running uvicorn (lifespan off, postgres down): streamed 17s of Windows-TTS-generated clinical audio through the real protocol — 4 VAD chunks, transcripts chained correctly, 7 field updates (name, age, chief_complaint, symptoms, vitals.blood_pressure, allergies, tentative_doctor_diagnosis), clean `session_complete`. A transient network failure on one chunk was retried, reported gracefully, and the session still completed.

### Note on venv

`.venv` was rebuilt with only light deps (fastapi, uvicorn, psycopg2-binary, cryptography, httpx, ruff, pytest) — the heavy ICD stack (torch, chromadb, spacy, sentence-transformers) is NOT installed locally, so `icd_coding_service` warmup can't run on this machine right now. `scripts/setup.py` installs everything.

---

## Key file locations

| File | Purpose |
|---|---|
| `scripts/setup.py` | One-command bootstrap (venv, deps, env, postgres, key validation) |
| `scripts/validate_gemini.py` | Live Gemini API validation (key, transcribe, extract, pipeline) |
| `backend/app/main.py` | FastAPI app, websocket endpoint, lifespan |
| `backend/app/services/gemini_client.py` | Gemini Interactions API REST client (retries, status checks) |
| `backend/app/services/gemini_transcribe_service.py` | VAD, audio chunking, transcribe + extract pipeline, WS protocol |
| `backend/app/services/summarizer.py` | Gemini summaries + clinical note drafts |
| `backend/app/services/icd_coding_service.py` | ICD-10-CM 3-tier coding (TF-IDF + ChromaDB + scispacy) |
| `backend/app/services/procedure_coding_service.py` | ICD-10-PCS 3-tier coding |
| `backend/app/database.py` | PostgreSQL + AES encryption + CRUD |
| `backend/app/api/routes.py` | `/api/generate-note` endpoint |
| `backend/app/api/ehr.py` | EHR CRUD, FHIR export, billing, analytics |
| `backend/.env` | Local config (GEMINI_API_KEY, AES key, models, VAD) |
| `backend/.env.example` | Template for env file |
| `docker-compose.yml` | 3 services: postgres, backend, frontend |
| `frontend/app/page.tsx` | Main scribe page |
| `frontend/hooks/useAudioStream.ts` | AudioWorklet mic capture |
| `frontend/hooks/useSocket.ts` | WebSocket connection to backend |
| `frontend/lib/claimsEngine.ts` | 9 insurance scheme rule engines |

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

---

## Remaining polish items

### Critical
- [ ] Tests for frontend (zero coverage)
- [ ] Backend integration tests (WS endpoint via FastAPI TestClient with mocked Gemini)

### Known issues
- [ ] **Pre-existing ruff debt**: CI (`ruff check backend/`) fails on ~59 pre-existing errors in untouched files (`scheme_service.py`, `database.py`, `ehr.py`, `icd_coding_service.py`, `procedure_coding_service.py`, etc.). All files touched in this session are clean. Fix separately to keep this branch's diff focused.
- [ ] Chunk boundaries can split values (e.g. "130 over 85" → `vitals.blood_pressure = "130/--"`). Inherent to VAD chunking; could be improved with cross-chunk context stitching.
- [ ] `frontend/node_modules` not installed on this machine — frontend lint/tsc not run locally (only a comment changed).

### Security
- [ ] CORS allows all origins in some paths; no API authentication on REST or WebSocket endpoints
- [ ] Hardcoded default DB credentials in `docker-compose.yml`
- [ ] `print()` used instead of `logging` in `database.py`
- [ ] Audio is sent to Google's cloud API — update privacy posture docs if that matters for deployment (the old pitch was "local-first")

### Code quality
- [ ] Hardcoded `http://localhost:8003` in frontend — needs central API config
- [ ] Heavy `any` type usage in TypeScript components
- [ ] `claimsEngine.ts` (~1014 lines) and `page.tsx` (~664 lines) are large single files
- [ ] `init_db()` called in lifespan; SQL via f-string for column names in `database.py`
- [ ] Duplicated logic: `scheme_service.py` and `claimsEngine.ts` both implement insurance eligibility
