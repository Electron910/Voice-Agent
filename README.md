# VoiceAI Clinical Agent

A real-time, multilingual **speech-to-speech (STS) voice agent** for clinical appointment management. Patients speak naturally in **English, Hindi, or Tamil** to **book, reschedule, cancel, or list** doctor appointments. The agent can also run **outbound reminder and follow-up campaigns**.

Built on **FastAPI (asyncio) + WebSockets**, **Deepgram** (STT/TTS), **Google Gemini 2.0 Flash-Lite** (reasoning + tool selection), **Redis** (two-tier memory), **PostgreSQL** (transactional data), and **Celery** (scheduled campaigns).

> **Latency target:** **< 450 ms** from end of user speech to first audio byte. Every turn logs a per-stage breakdown (STT → Agent → Tools → TTS first byte).


## Features

- **Real-time voice in/out** over a single full-duplex WebSocket (16-bit PCM, 16 kHz, mono).
- **Multilingual**: English, Hindi, and Tamil, with automatic per-turn language detection.
- **LLM agent with tool calling**: intent detection, slot filling, and DB-backed actions.
- **Streaming TTS**: responses are split into sentences and streamed so playback starts early.
- **Barge-in / interruption**: the user can cut the agent off mid-sentence.
- **Two-tier memory**: short-lived session state plus long-lived patient profile.
- **Graceful degradation**: a rule-based state-machine fallback takes over if the LLM fails.
- **Outbound campaigns**: Celery Beat schedules daily reminders and weekly follow-ups.
- **Per-stage latency logs** for every turn via `structlog`.

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| API / Realtime | FastAPI, Uvicorn (`uvloop`), WebSockets | Async I/O; a single persistent bidirectional channel for audio and control |
| Speech-to-Text | Deepgram `nova-2` | Fast, accurate, supports `en-IN` / Hindi / Tamil and language detection |
| Text-to-Speech | Deepgram Aura (`aura-asteria-en`), `linear16` | Low time-to-first-byte; raw PCM avoids decode overhead |
| Reasoning | Google Gemini `gemini-2.0-flash-lite` (JSON mode) | Low-latency model; structured JSON output for reliable parsing |
| Language detection | `langdetect` + Deepgram detection | Per-turn language switching |
| Session + persistent memory | Redis 7 (`orjson` serialization) | Sub-millisecond reads; TTL-based expiry |
| Transactional DB | PostgreSQL 17, SQLAlchemy 2 (async) + `asyncpg`, Alembic | ACID bookings, relational integrity, migrations |
| Background jobs | Celery 5 + Celery Beat (Redis broker) | Scheduled outbound campaigns outside the request path |
| Logging | `structlog` | Structured, key-value logs keyed by `session_id` |
| Audio utils | NumPy, soundfile | Silence detection (VAD), WAV framing, normalization |
| Testing | pytest, pytest-asyncio, `unittest.mock` | Deterministic agent tests without live LLM calls |
| Deploy | Docker, Docker Compose, Render | Reproducible local stack plus managed cloud hosting |

---

## System Architecture

```mermaid
flowchart TD
    User((Patient)) <-->|Speaks / Listens| Browser["Browser Client<br/>Web Audio API"]
    Browser <-->|"WebSocket /ws/{session_id}<br/>binary PCM + JSON control"| WS[WebSocket Handler]

    subgraph Backend["FastAPI Backend (asyncio)"]
        WS --> VAD["Audio Processor<br/>VAD + WAV framing"]
        VAD --> STT["STT Service<br/>Deepgram nova-2"]
        STT --> LD[Language Detector]
        LD --> ORC[Agent Orchestrator]
        ORC <--> MM[Memory Manager]
        ORC <--> RE[Reasoning Engine]
        ORC <--> TR[Tool Registry]
        ORC --> TTS["TTS Service<br/>Deepgram Aura (streaming)"]
        TTS --> WS
        LT[Latency Tracker] -.measures.- WS
    end

    RE <-->|JSON mode| GEM[Gemini 2.0 Flash-Lite]
    RE -.on failure.-> FB[Rule-based State Machine Fallback]

    MM <--> R1[("Redis<br/>session:* (TTL 1h)")]
    MM <--> R2[("Redis<br/>patient_memory:* (TTL 90d)")]
    TR <--> PG[("PostgreSQL<br/>patients, doctors, schedules,<br/>appointments, interactions, campaigns")]

    subgraph Async["Background Workers"]
        BEAT[Celery Beat] --> WORKER[Celery Worker]
    end
    WORKER <--> PG
    WORKER --> ORC
    BEAT <-->|broker| RQ[(Redis db 1/2)]
```

**Layers at a glance**

| Layer | Responsibility | Key files |
|---|---|---|
| Transport | Connection lifecycle, audio buffering, protocol | `backend/api/websocket_handler.py` |
| Speech | VAD, STT, TTS, language detection | `backend/services/*` |
| Agent | Context building, LLM reasoning, tool dispatch, response formatting | `backend/agent/*` |
| Memory | Session and persistent memory | `backend/memory/*` |
| Data | ORM models, DB session, migrations | `backend/models.py`, `backend/database.py`, `migrations/` |
| Scheduling | Booking validation, conflict resolution, campaigns | `backend/scheduler/*` |
| Observability | Per-stage latency tracking | `backend/middleware/latency_tracker.py` |

---

## Voice Pipeline (End-to-End Turn)

```mermaid
sequenceDiagram
    participant B as Browser
    participant W as WebSocket Handler
    participant S as STT (Deepgram)
    participant O as Orchestrator
    participant M as Memory (Redis)
    participant L as Gemini
    participant T as Tools (Postgres)
    participant V as TTS (Deepgram)

    B->>W: binary PCM chunks (16 kHz, 16-bit, mono)
    W->>W: VAD: 800 ms silence → speech end
    Note over W: t0 = speech_end_timestamp
    W->>S: WAV-framed utterance (5 s timeout)
    S-->>W: transcript + detected language
    W-->>B: {"type":"transcript"}
    W->>O: process_turn(text, language)
    O->>M: build_context (session + profile + last 3 interactions)
    O->>L: prompt (state, slots, last 6 turns) → JSON (12 s timeout)
    L-->>O: intent, tool_calls, slots, response_text
    O->>T: execute tools (2 s per tool, 5 s overall)
    T-->>O: tool results
    O-->>W: final response_text
    O--)M: background update (asyncio.create_task)
    W-->>B: {"type":"response"}
    W->>V: sentence-split streaming synthesis
    V-->>W: first audio chunk
    Note over W: t1 = first_audio_response
    W-->>B: binary PCM audio chunks
    W-->>B: {"type":"latency", data: breakdown}
```

**Stages**

1. **Capture**: the browser streams raw 16-bit PCM chunks over the WebSocket.
2. **Endpointing (VAD)**: `AudioProcessor.detect_speech_end` flags speech end after **800 ms** of samples below amplitude threshold **500**. The client can also send `speech_end` explicitly.
3. **STT**: buffered PCM is wrapped in a WAV header and sent to Deepgram `nova-2` with language detection.
4. **Language detection**: `langdetect` re-checks the transcript and falls back to the session language.
5. **Reasoning**: the orchestrator builds context and Gemini returns **structured JSON** (intent, tool calls, slots, reply).
6. **Tool orchestration**: tools run against PostgreSQL with strict timeouts. Missing IDs are injected from session slots.
7. **Streaming TTS**: the reply is split on sentence boundaries. Each sentence is synthesized and streamed, so the first sentence plays while later ones are generated.
8. **Persistence**: memory updates run **in the background**, off the response path.

---

## Latency Breakdown & Measurement

**Definition:** `total_ms = first_audio_response − speech_end_timestamp`. This covers the server-side time from detected end of speech to the first TTS audio byte sent to the client.

### Measured stages (`LatencyBreakdown`)

| Field | Measures | Start → End marker |
|---|---|---|
| `stt_ms` | Speech-to-text | `stt_start` → `stt_end` |
| `agent_ms` | Context + LLM reasoning | `agent_start` → `agent_end` |
| `tool_ms` | Tool execution (DB) | `tool_exec_start` → `tool_exec_end` |
| `tts_first_byte_ms` | TTS time-to-first-byte | `tts_start` → `tts_first_byte` |
| `total_ms` | End-to-end | `speech_end_timestamp` → `first_audio_response` |
| `under_target` | `total_ms < 450` | — |

Timestamps use `time.perf_counter()` (monotonic, high resolution). Each turn emits:

- `latency_report` (INFO) when under target, or `latency_exceeded` (WARNING) when over.
- A `{"type": "latency"}` WebSocket message to the client for live display.

**Sample log line** (structure only; values vary by network and region):

```json
{
  "event": "latency_report",
  "session_id": "3f2a...",
  "total_ms": "<ms>",
  "stt_ms": "<ms>",
  "agent_ms": "<ms>",
  "tool_ms": "<ms>",
  "tts_first_byte_ms": "<ms>",
  "stages": { "stt_start": "...", "stt_end": "...", "tts_first_byte": "..." },
  "under_target": true
}
```

### Latency techniques

| Technique | Where | Effect |
|---|---|---|
| Lightweight LLM (Flash-Lite) + JSON mode | `agent/reasoning.py` | Short generation time, no filler tokens |
| Sliding context window (last **6** turns) | `agent/reasoning.py` | Prompt size stays flat as calls get longer |
| Model **warm-up** on startup | `main.py` lifespan → `reasoning_engine.warmup()` | Avoids cold-start on the first call |
| Sentence-level **streaming TTS** | `services/tts_service.py` | First audio plays before the full reply is synthesized |
| Raw `linear16` PCM output | `services/tts_service.py` | No client-side decode step |
| Background memory writes | `orchestrator._background_update` | Persistence is off the critical path |
| Redis + `orjson` for memory | `memory/*` | Fast context reads and serialization |
| Strict timeouts (STT 5 s, tool 2 s, tools 5 s, LLM 12 s) | handler / orchestrator / reasoning | Stops tail latency from hanging a call |
| Pooled `httpx` client with tight timeouts | `services/tts_service.py` | Connection reuse; fast failure |
| Composite DB indexes | `models.py` | Fast availability and appointment lookups |

---

## Two-Tier Memory Design

The LLM is **stateless**. Memory is rebuilt and injected into the prompt every turn by `MemoryManager.build_context()`.

```mermaid
flowchart LR
    subgraph T1["Tier 1: Session Memory (short-term)"]
        S["Redis key: session:{session_id}<br/>TTL: 1 hour"]
    end
    subgraph T2["Tier 2: Persistent Memory (long-term)"]
        P["Redis key: patient_memory:{patient_id}<br/>TTL: 90 days"]
        I["Redis key: patient_interactions:{patient_id}<br/>last 50 interactions"]
    end
    ORC[Orchestrator] -->|build_context| S
    ORC -->|build_context| P
    ORC -->|last 3| I
    ORC -.->|update_after_turn (background)| S
    ORC -.->|update_after_turn (background)| P
    ORC -.->|update_after_turn (background)| I
```

### Tier 1: Session memory (`memory/session_memory.py`)

Holds the state of the current conversation.

| Field | Purpose |
|---|---|
| `patient_id`, `language` | Who is calling, in what language |
| `current_intent` | book / cancel / reschedule / list … |
| `conversation_state` | `greeting → collecting_info → confirming → executing → completed` |
| `collected_slots` | specialization, doctor_id, date, time_slot, appointment_id … |
| `turn_history` | Capped at **20** turns; the last **6** go into the prompt |
| `tool_results`, `pending_confirmations`, `interruption_flag` | Turn-level control state |

### Tier 2: Persistent memory (`memory/persistent_memory.py`)

Holds cross-session knowledge about the patient.

| Field | Purpose |
|---|---|
| `preferred_language` | Greets returning patients in their language |
| `preferred_doctors` (max 10) | Personalization |
| `preferred_time_slots`, `medical_notes` | Personalization / context |
| `interaction_count`, `last_interaction` | Engagement tracking |
| Interaction log (max 50; last 3 injected) | Short summaries of previous calls |

> **Source of truth:** Appointments, schedules, and patient records live in **PostgreSQL**. The Redis tiers hold conversational context and preferences only.

**Memory lifecycle per turn**
1. `build_context()` reads session, profile, and the last 3 interactions.
2. The reasoning engine receives state, slots, and the last 6 turns.
3. `update_after_turn()` appends turns, updates intent and language preference, records the interaction, and bumps counters. It runs as a background task.

---

## Agent Orchestration & Tools

`AgentOrchestrator.process_turn()` (`agent/orchestrator.py`) coordinates each turn:

1. Build context from memory.
2. Call `ReasoningEngine.reason()`, which returns JSON:
   ```json
   {
     "reasoning": "why",
     "intent": "book|cancel|reschedule|check_availability|list|greeting|clarification|confirmation|farewell",
     "tool_calls": [{"tool": "search_doctors", "parameters": {"specialization": "cardiologist"}}],
     "response_text": "reply in the user's language",
     "slots_extracted": {},
     "needs_confirmation": false,
     "conversation_state": "greeting|collecting_info|confirming|executing|completed"
   }
   ```
3. Validate tool calls and inject missing `doctor_id` / `patient_id` from session slots.
4. Execute tools (2 s per tool, 5 s total).
5. Merge tool results into session slots and format a localized reply (en / hi / ta).
6. Return the response and schedule the memory update in the background.

### Tool registry (`agent/tools.py`)

| Tool | Parameters | Description |
|---|---|---|
| `search_doctors` | `specialization` | Find doctors by specialization |
| `check_availability` | `doctor_id?`, `specialization?`, `date?` | Free slots for a doctor and date |
| `book_appointment` | `patient_id`, `doctor_id`, `date`, `time_slot` | Validates and books a slot |
| `cancel_appointment` | `appointment_id` | Cancels and frees the slot |
| `reschedule_appointment` | `appointment_id`, `new_date`, `new_time_slot` | Moves an appointment |
| `list_appointments` | `patient_id` | Upcoming appointments |

Supporting logic: `scheduler/appointment_engine.py` (slot lookup, booking validation) and `scheduler/conflict_resolver.py` (suggests alternative slots on conflict).

> **No RAG / vector store.** Clinic data is structured, so retrieval uses **LLM tool calling + exact SQL queries**. Results are deterministic and always current.

---

## Fallbacks & Resilience

| Failure | Fallback | Location |
|---|---|---|
| Gemini error, timeout, or missing API key | Rule-based **state machine** (`_stateful_fallback`): keyword intent detection, regex slot extraction, templated multilingual replies | `agent/reasoning.py` |
| LLM omits `doctor_id` / `patient_id` | Injected from session slots; otherwise a safe error tool result | `agent/orchestrator.py` |
| Tool / DB hangs | `asyncio.wait_for` timeout → `{"success": false}` → graceful spoken reply | `agent/orchestrator.py` |
| STT timeout or empty transcript | User-facing error event ("please try again") | `api/websocket_handler.py` |
| Audio too short (< 0.1 s) | Rejected before STT | `api/websocket_handler.py` |
| Unhandled agent exception | Generic apology response; session stays alive | `api/websocket_handler.py` |
| Browser audio decode failure | Manual `Int16 → Float32` raw-PCM playback | Frontend (`main.py`) |
| Uncertain language | STT detection → `langdetect` → session/profile language → `en` | `services/language_detection.py` |

---

## Database Schema

PostgreSQL via SQLAlchemy (async). Migrations live in `migrations/versions/001_initial.py`.

```mermaid
erDiagram
    PATIENTS ||--o{ APPOINTMENTS : books
    DOCTORS  ||--o{ APPOINTMENTS : attends
    DOCTORS  ||--o{ DOCTOR_SCHEDULES : has
    PATIENTS ||--o{ INTERACTIONS : logs

    PATIENTS {
        uuid id PK
        string name
        string phone UK
        string preferred_language
        json metadata
        datetime created_at
        datetime updated_at
    }
    DOCTORS {
        uuid id PK
        string name
        string specialization
        json available_days
        int slot_duration_minutes
        json metadata
    }
    DOCTOR_SCHEDULES {
        uuid id PK
        uuid doctor_id FK
        datetime date
        json available_slots
        json booked_slots
    }
    APPOINTMENTS {
        uuid id PK
        uuid patient_id FK
        uuid doctor_id FK
        datetime date
        string time_slot
        enum status
        text notes
        datetime created_at
        datetime updated_at
    }
    INTERACTIONS {
        uuid id PK
        uuid patient_id FK
        string session_id
        string direction
        string language
        json transcript
        json actions_taken
        json latency_log
        datetime created_at
    }
    CAMPAIGNS {
        uuid id PK
        string name
        string campaign_type
        json target_patients
        text message_template
        enum status
        datetime scheduled_at
        datetime completed_at
        json results
        datetime created_at
    }
```

**Indexes**

| Index | Columns | Used for |
|---|---|---|
| `patients.phone` | unique | Caller lookup |
| `doctors.specialization` | — | `search_doctors` |
| `idx_doctor_date` | `doctor_schedules(doctor_id, date)` | Availability checks |
| `idx_doctor_datetime` | `appointments(doctor_id, date, time_slot)` | Double-booking checks |
| `idx_patient_status` | `appointments(patient_id, status)` | Upcoming appointments |
| `interactions.session_id` | — | Session audit lookup |

**Enums:** `AppointmentStatus` = `scheduled | cancelled | completed | rescheduled | no_show`; `CampaignStatus` = `pending | in_progress | completed | failed`.

---

## API Reference

Base URL: `http://localhost:8000`. Interactive docs: **`/docs`** (Swagger) and **`/redoc`**.

### Core REST (`/api`)

| Method | Endpoint | Body / Query | Description |
|---|---|---|---|
| GET | `/api/health` | — | Health check (used by Render) |
| POST | `/api/patients` | `{name, phone, preferred_language}` | Create patient |
| GET | `/api/patients/{patient_id}` | — | Get patient |
| POST | `/api/doctors` | `{name, specialization, available_days, slot_duration_minutes}` | Create doctor |
| GET | `/api/doctors` | `?specialization=` | List or filter doctors |
| POST | `/api/schedules` | `{doctor_id, date: YYYY-MM-DD, available_slots: [...]}` | Create a day's schedule |
| GET | `/api/availability` | `?doctor_id=&specialization=&date=` | Available slots |
| POST | `/api/appointments/book` | `{patient_id, doctor_id, date, time_slot}` | Book (400 on conflict) |
| GET | `/api/appointments/{patient_id}` | — | List patient appointments |
| POST | `/api/seed` | — | Seed 5 doctors, 14 days of schedules, 3 patients |

### Campaigns (`/api/campaigns`)

| Method | Endpoint | Body | Description |
|---|---|---|---|
| POST | `/api/campaigns/create` | `{campaign_type: "reminder" \| "followup", hours_before: 24, days_after: 7}` | Build campaign target list |
| POST | `/api/campaigns/simulate-outbound` | `{campaign_id, patient_index: 0, patient_response: ""}` | Simulate one outbound turn |
| GET | `/api/campaigns/{campaign_id}` | — | Campaign targets |

### Other

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Built-in browser test client (mic, transcript, latency panel) |
| WS | `/ws/{session_id}` | Voice session with a client-provided ID |
| WS | `/ws` | Voice session with a server-generated ID |

**Example**

```bash
curl -X POST http://localhost:8000/api/seed
curl "http://localhost:8000/api/availability?specialization=Cardiologist&date=2026-11-02"
curl -X POST http://localhost:8000/api/appointments/book \
  -H "Content-Type: application/json" \
  -d '{"patient_id":"<uuid>","doctor_id":"<uuid>","date":"2026-11-02","time_slot":"10:00"}'
```

---

## WebSocket Protocol

**Audio format:** binary frames, PCM **16-bit signed little-endian, 16 kHz, mono**, in both directions.

### Client → Server

| Frame | Payload | Purpose |
|---|---|---|
| JSON | `{"type":"init","patient_id":"...","language":"en\|hi\|ta"}` | Initialize session; loads preferred language |
| Binary | PCM chunk | Streamed microphone audio |
| JSON | `{"type":"speech_end"}` | Force end-of-utterance processing |
| JSON | `{"type":"text","content":"..."}` | Text input (skips STT) |
| JSON | `{"type":"interrupt"}` | Barge-in: stop current TTS stream |

### Server → Client

| Frame | Payload |
|---|---|
| JSON | `{"type":"connected","session_id":"..."}` |
| JSON | `{"type":"initialized","language":"..."}` |
| JSON | `{"type":"transcript","text":"...","language":"..."}` |
| JSON | `{"type":"response","text":"...","intent":"...","reasoning":"...","tool_calls":[],"tool_results":[],"language":"...","conversation_state":"..."}` |
| Binary | TTS audio chunks |
| JSON | `{"type":"latency","data":{...breakdown...}}` |
| JSON | `{"type":"interrupted"}` / `{"type":"error","message":"..."}` |

---

## Project Structure

```
VoiceAI/
├── backend/
│   ├── main.py                    # FastAPI app, lifespan (DB init, memory init, LLM warm-up), test UI, WS routes
│   ├── config.py                  # Pydantic settings (.env)
│   ├── database.py                # Async engine & session factory
│   ├── models.py                  # SQLAlchemy ORM models
│   ├── agent/
│   │   ├── orchestrator.py        # Turn coordination, tool dispatch, response formatting
│   │   ├── reasoning.py           # Gemini prompting + rule-based fallback
│   │   ├── tools.py               # Tool registry & DB-backed tools
│   │   └── prompts.py             # Language instructions / prompt pieces
│   ├── api/
│   │   ├── websocket_handler.py   # Realtime voice pipeline
│   │   ├── rest_routes.py         # CRUD, availability, booking, seed
│   │   └── campaign_routes.py     # Campaign APIs & outbound simulation
│   ├── memory/
│   │   ├── memory_manager.py      # Facade over both tiers
│   │   ├── session_memory.py      # Tier 1 (Redis, 1h TTL)
│   │   └── persistent_memory.py   # Tier 2 (Redis, 90d TTL)
│   ├── middleware/
│   │   └── latency_tracker.py     # Per-stage latency breakdown
│   ├── scheduler/
│   │   ├── appointment_engine.py  # Slot lookup & validation
│   │   ├── conflict_resolver.py   # Alternative slot suggestions
│   │   ├── campaign_scheduler.py  # Reminder / follow-up campaign builders
│   │   └── celery_worker.py       # Celery app, beat schedule, tasks
│   └── services/
│       ├── audio_processor.py     # VAD, WAV header, normalization
│       ├── stt_service.py         # Deepgram STT
│       ├── tts_service.py         # Deepgram TTS (streaming)
│       └── language_detection.py  # langdetect wrapper
├── migrations/                    # Alembic migrations
├── tests/                         # pytest suites
├── docker-compose.yml
├── Dockerfile.backend
├── render.yaml
├── requirements.txt
└── .env.example
```

---

## Configuration

Copy `.env.example` to `.env` and fill in the values:

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | — | **Required** for LLM reasoning (without it, the rule-based fallback runs) |
| `DEEPGRAM_API_KEY` | — | **Required** for STT/TTS |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/voiceai` | PostgreSQL DSN |
| `REDIS_URL` | `redis://localhost:6379/0` | Memory store |
| `REDIS_SESSION_TTL` | `3600` | Session memory TTL (seconds) |
| `REDIS_MEMORY_TTL` | `7776000` | Persistent memory TTL (90 days) |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/2` | Celery results |
| `STT_PROVIDER` / `TTS_PROVIDER` | `deepgram` | Speech providers |
| `TARGET_LATENCY_MS` | `450` | Latency budget |
| `AUDIO_SAMPLE_RATE` / `AUDIO_CHANNELS` | `16000` / `1` | Audio format |
| `DEBUG` | `false` | Debug mode |

> Never commit `.env`. It is listed in `.gitignore`.

---

## Local Development

### Prerequisites
- Python **3.12**, Docker and Docker Compose
- Deepgram and Google Gemini API keys

### Option A: Full stack with Docker Compose

```bash
cp .env.example .env            # add GEMINI_API_KEY and DEEPGRAM_API_KEY
docker compose up --build
```

This starts `postgres`, `redis` (256 MB, `allkeys-lru`), `backend` (:8000), `celery-worker`, and `celery-beat`.

### Option B: Run the backend locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d postgres redis      # infrastructure only
alembic upgrade head                     # apply migrations
uvicorn backend.main:app --reload --port 8000

# optional: background workers (separate terminals)
celery -A backend.scheduler.celery_worker worker --loglevel=info
celery -A backend.scheduler.celery_worker beat   --loglevel=info
```

### Try it
1. `curl -X POST http://localhost:8000/api/seed`
2. Open **http://localhost:8000/** and pick a language.
3. Click the mic and say: *"I want to book an appointment with a cardiologist tomorrow at 10 AM."*
4. Watch the transcript, agent reasoning, tool calls, and the **latency panel**.

---

## Deployment Guide

### Docker (any VM / container platform)

```bash
docker build -f Dockerfile.backend -t voiceai-backend .
docker run -p 8000:8000 --env-file .env voiceai-backend
# Image runs: uvicorn backend.main:app --workers 4 --loop uvloop
```

Run Celery worker and beat from the same image by overriding the command (see `docker-compose.yml`).

### Render (`render.yaml` blueprint)

1. Push the repo to GitHub and create a **Blueprint** on Render from `render.yaml`.
2. This provisions:
   - **`voiceai-backend`** web service (Python 3.12, health check `/api/health`)
   - **`voiceai-redis`** (`allkeys-lru`), wired to `REDIS_URL` and the Celery URLs automatically
3. Set secrets in the dashboard: `GEMINI_API_KEY`, `DEEPGRAM_API_KEY`, `DATABASE_URL` (managed Postgres, `postgresql+asyncpg://...`).
4. Run migrations once: `alembic upgrade head`.
5. Seed demo data: `POST /api/seed`.

### Production checklist
- [ ] Serve over **HTTPS / WSS** (browsers require a secure context for microphone access).
- [ ] Use sticky sessions or shared Redis for WebSocket scaling (session state is already in Redis).
- [ ] Restrict CORS origins and add authentication to REST and WS endpoints.
- [ ] Use managed Postgres with backups; size the Redis eviction policy for your workload.
- [ ] Run Celery worker and beat as separate services, with only **one** beat instance.
- [ ] Ship `structlog` output to a log aggregator and alert on `latency_exceeded`.
- [ ] Keep PHI handling compliant (encryption at rest and in transit, retention policies).

---

## Testing

```bash
pytest -v
```

| Suite | Covers |
|---|---|
| `tests/test_agent.py` | Intent extraction, tool dispatch, orchestrator turns (LLM mocked) |
| `tests/test_memory.py` | Session and persistent memory behavior |
| `tests/test_latency.py` | Latency breakdown math, tracker lifecycle, `under_target` flag |
| `tests/test_scheduler.py` | Slot validation and conflict resolution |

LLM calls are mocked with `unittest.mock`, so tests are fast, deterministic, and free.

---

## Observability

All logs are **structured** (`structlog`) and carry `session_id`, so one call can be traced end to end:

| Event | Meaning |
|---|---|
| `ws_connected` / `ws_disconnected` | Session lifecycle |
| `audio_buffer_stats` | Chunks, bytes, estimated duration |
| `stt_result` / `stt_timeout` / `stt_error_detailed` | STT outcome |
| `gemini_failed` | LLM failure → fallback engaged |
| `injected_doctor_id_from_session` | Orchestrator repaired a tool call |
| `latency_report` / `latency_exceeded` | Per-turn stage breakdown vs. the 450 ms target |
| `tts_error`, `agent_error` | Downstream failures |
