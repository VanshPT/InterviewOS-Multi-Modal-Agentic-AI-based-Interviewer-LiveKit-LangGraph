# InterviewOS: Multi-Modal Agentic Interviewer (Voice + Text)

**InterviewOS** is an end-to-end mock interview system that supports both **text interviews** (web UI) and **real-time voice interviews** (mic → STT → agent → TTS), while persisting a complete transcript and interview state in the backend.

---

## 🔥 Why this project
Most mock interview apps either:
- do **text only**, or
- do **voice only** without reliable stage control, persistence, or fallbacks.

InterviewOS is built like a real product feature: **stateful**, **recoverable**, and **orchestrated** across multiple AI components.

---

## 🧩 High-Level Architecture (Agentic Orchestration)

### 1) Client (Web UI)
- `demo.html` provides **two modes**:
  - **Text Mode:** user types answers → UI calls backend for next turn.
  - **Voice Mode:** user joins a LiveKit room → mic streams audio → transcript appears live.

### 2) Realtime Voice Layer (LiveKit)
- LiveKit handles:
  - realtime audio transport (WebRTC)
  - **STT** (speech → text)
  - **TTS** (text → speech)
- A local **LiveKit Agent worker** joins the room automatically and acts as a **thin routing agent**.

### 3) Interview Brain (Django + LangGraph + Gemini)
- The backend provides the **single source of truth** for interview logic.
- A **LangGraph controller** enforces stage rules and transitions.
- Gemini generates the actual interviewer prompts.
- Every message is stored as persistent history (`InterviewSession` + `InterviewMessage`).

---

## ✅ Core Features
- **Multi-Modal Interview:** seamless **Text + Voice** interview experience.
- **Two-stage flow:** Implements:
  - **Self-Introduction**
  - **Past Experience**
- **Smooth transitions:** deterministic switching logic, no prompt conflicts.
- **Time-based fallback:** if switching logic isn’t triggered, the backend forces a safe transition so the workflow never stalls.
- **Anti-repeat guardrails:** fingerprinting + retry/fallback logic prevents repetitive questions.
- **Persistent “session memory”:** full transcript and state are stored with timestamps + stage labels.
- **Secure ingestion:** voice worker calls backend through a protected endpoint using `X-INGEST-SECRET`.

---

## 🔁 Voice Pipeline (How voice mode works)
1. User clicks **Start Voice** in `demo.html`.
2. UI fetches a **LiveKit token** from Django and joins the room.
3. LiveKit Agent worker is dispatched automatically and joins the same room.
4. Worker calls backend `/engine/next_turn/` with `event_type="start"`.
5. Backend returns `assistant_text` → worker sends it to **TTS** → user hears it.
6. User speaks → LiveKit runs **STT** → transcript produced.
7. Worker sends transcript to backend (`event_type="user_turn"`).
8. Backend returns next `assistant_text` → TTS speaks it.
9. Transcript is visible live in the UI from DB polling.

---

## 🧠 “Multi-Agent / Multi-AI Orchestration” (Recruiter Keywords, accurately framed)
This system is **agentic AI orchestration** across multiple specialized AI components:

- **STT model** (speech-to-text)
- **LLM orchestration + generation** (LangGraph controller + Gemini)
- **TTS model** (text-to-speech)
- **Realtime orchestration layer** (LiveKit)

Even though the voice worker behaves like a single router agent, the overall design is a **multi-model, multi-service orchestration pipeline** (multi-modal agentic system).

---

## 🛠 Tech Stack
- **Frontend:** HTML/CSS/JS (text + voice UI)
- **Backend:** Django (REST endpoints + DB persistence)
- **State Machine:** LangGraph
- **LLM:** Gemini
- **Realtime Voice:** LiveKit
- **STT/TTS:** LiveKit-integrated providers (configurable)

---

## ⭐ What makes it “product-grade”
- deterministic stage transitions + fallbacks
- persistent transcript + replayable sessions
- secure tool ingestion for voice agent
- separation of concerns:
  - LiveKit = real-time voice IO
  - Django + LangGraph = interview “brain”
  - UI = unified transcript view (text or voice)