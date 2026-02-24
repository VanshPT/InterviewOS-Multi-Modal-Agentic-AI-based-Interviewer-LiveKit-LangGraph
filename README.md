# InterviewOS — Multi-Modal Agentic Mock Interview (Text + Voice)

A product-style mock interview system that supports **text interviews** (web UI) and **real-time voice interviews** (mic → STT → backend engine → TTS) while persisting a complete transcript and session state in a database.

> **Key idea:** The **Django backend is the single source of truth** for interview logic.  
> Voice is an “overhead layer” (LiveKit + local agent worker) that **routes** audio to/from the same backend engine that powers text mode.

---

## Demo Modes

### 1) Text Mode (Phase 3)
- Use the web UI (`demo.html`) to:
  - Create session
  - Start interview
  - Type answers and receive interviewer responses
- Transcript updates live from DB.

### 2) Voice Mode (Phase 4 + 5)
- Use the same web UI (`demo.html`) to:
  - Create session
  - Click **Start Voice**
  - Speak answers (STT) and hear the interviewer (TTS)
- Transcript is still shown live from DB, identical to text mode.

---

## High-Level Architecture (Agentic Orchestration)

### Components
1) **Frontend UI (demo.html)**
- Text mode: calls backend UI endpoint for turn-by-turn interview.
- Voice mode: connects to LiveKit using a token from backend.
- Transcript panel polls backend messages endpoint.

2) **Backend (Django)**
- Stores session + messages.
- Runs the interview engine for every next turn.
- Enforces time-based fallback transitions so the interview never gets stuck.

3) **Interview Engine (engine.py)**
- Uses **Google GenAI SDK** (`google.genai`) for generation.
- Maintains interview stages using:
  - **LLM “signal tokens”** at the end of every model reply:
    - `<<<STAY>>>`
    - `<<<MOVE_TO_EXPERIENCE>>>`
    - `<<<MOVE_TO_DONE>>>`
  - **Hard guardrails** based on agent turn counts per stage.
  - **Timeout fallback** driven by backend (views.py).

4) **Voice Worker (LiveKit Agent Worker: agent.py)**
- A local agent worker joins the LiveKit room (dispatched via token endpoint).
- Uses **STT** to transcribe your speech into text.
- Calls Django engine endpoint (`/api/interview/engine/next_turn/`).
- Speaks backend’s `assistant_text` via **TTS**.

---

## State Machine (How Stages Work)

Current stages:
- `intro` → self-introduction
- `experience` → past experience deep dive
- `done` → wrap-up feedback

### How transitions happen today (no LangGraph)
This project does **not** use LangGraph.  
Instead, stage transitions are implemented as:

1) **LLM-driven signals**
- The system prompt requires the model to end every reply with a signal token:
  - `<<<STAY>>>` (continue stage)
  - `<<<MOVE_TO_EXPERIENCE>>>` (intro → experience)
  - `<<<MOVE_TO_DONE>>>` (experience → done)

2) **Guardrails (hard fallback)**
- Backend counts interviewer turns per stage.
- If max turns exceeded, it forces stage transitions even if LLM didn’t signal.

3) **Time-based fallback**
- Backend checks how long the stage has been running.
- If exceeded, it forces `event_type="timeout"` into the engine so the interview never stalls.

> **Future upgrade path (optional):**  
> If you add **non-linear** states (branching flows: behavioral vs system design vs coding, retries, scoring loops), migrating the controller to **LangGraph** becomes useful because it formalizes graph-based routing and makes complex transitions easier to reason about.  
> Today, the state space is intentionally small and mostly linear, so a lightweight “signal + guardrails + timeout” controller is sufficient.

---

## Data Model

### InterviewSession
- `room_name` (LiveKit room identifier)
- `candidate_name`, `role`
- `status` (`created/running/ended/error`)
- `stage` (`intro/experience/done`)
- `stage_started_at`, `last_turn_at`, `ended_at`

### InterviewMessage
- `session` FK
- `role` (`user/agent/system`)
- `stage`
- `text`
- `meta` (stores `event_id` for idempotency)

---

## API Endpoints

### Core
- `POST /api/interview/sessions/create/`
  - creates a session + initial system message

- `GET /api/interview/sessions/<session_id>/messages/`
  - returns transcript + session info

### Interview Engine
- `POST /api/interview/engine/next_turn/`
  - **protected** with header: `X-INGEST-SECRET`
  - used by voice worker (and any ingestion client)
  - supports: `event_type = start | user_turn | timeout`

- `POST /api/interview/ui/next_turn/`
  - text-mode endpoint for browser testing
  - only enabled when `DEBUG=True`

### LiveKit Token (Phase 4)
- `POST /api/interview/livekit/token/`
  - returns `{ url, token, room_name }` so the browser can connect to LiveKit
  - also dispatches the agent worker job (RoomConfiguration / RoomAgentDispatch)

---

## Local Setup

### 1) Backend env (`backend/.env`)


```env
LIVEKIT_URL=...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...

AGENT_NAME=...
INGEST_SECRET=...

GOOGLE_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
GEMINI_TEMPERATURE=0.7
GEMINI_MAX_OUTPUT_TOKENS=280

INTRO_TIMEOUT_S=90
EXPERIENCE_TIMEOUT_S=180
MAX_INTRO_TURNS=7
MAX_EXP_TURNS=14


### 2) Agent env (`agent/.env.local`)


```env
LIVEKIT_URL=...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
AGENT_NAME=...
INGEST_SECRET=...
GOOGLE_API_KEY=...
INTRO_TIMEOUT_S=90
EXPERIENCE_TIMEOUT_S=180
GEMINI_MODEL=gemini-2.5-flash

BACKEND_BASE_URL=http://127.0.0.1:8000
