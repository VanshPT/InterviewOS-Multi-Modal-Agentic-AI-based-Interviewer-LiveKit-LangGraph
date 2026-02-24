"""
AI Mock Interview Engine
========================
Uses google.genai (new SDK) with system_instruction + generate_content.
Built-in retry via tenacity. REST/httpx transport for low latency.

Two stages:
  1. intro   — self-introduction (4-6 Q&A rounds)
  2. experience — past-experience deep-dive (6-12 Q&A rounds)
  3. done    — wrap-up feedback

Stage transitions are driven by the LLM (via <<<SIGNAL>>> tokens)
with hard guard-rails as fallback.
"""
from __future__ import annotations

import os
import re
import time
import traceback
from typing import List, Dict, Any

from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Client config
# ---------------------------------------------------------------------------
_API_KEY = os.getenv("GOOGLE_API_KEY", "")
_client = genai.Client(api_key=_API_KEY)

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
MODEL_FALLBACKS = [MODEL_NAME, "gemini-2.5-flash-lite", "gemini-2.0-flash-lite"]
TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.7"))
MAX_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "280"))

# Guard-rails
MAX_INTRO_TURNS = int(os.getenv("MAX_INTRO_TURNS", "7"))
MAX_EXP_TURNS = int(os.getenv("MAX_EXP_TURNS", "14"))


# ---------------------------------------------------------------------------
# System prompt — the interviewer persona
# ---------------------------------------------------------------------------
def _system_prompt(name: str, role: str, stage: str) -> str:
    return f"""You are Taylor, a senior technical interviewer. You are warm, sharp, and professional.

CANDIDATE: {name}
TARGET ROLE: {role}
CURRENT STAGE: {stage}

PERSONALITY RULES:
- Sound like a real human interviewer, not a chatbot.
- Keep every reply SHORT: 1-3 sentences max.
- Ask exactly ONE question per reply.
- Briefly acknowledge the candidate's last answer before asking the next question (e.g. "Nice!", "Got it.", "That's interesting—").
- Never repeat a question you already asked.
- Never mention AI, prompts, systems, LLMs, stages, or internal tooling.
- Reference specific details from the candidate's answers to show you are listening.

STAGE INSTRUCTIONS:

If stage is "intro":
 - Greet the candidate warmly.
 - Ask about: who they are, education/background, why this role, key skills, what excites them.
 - Typically 4-6 exchanges total. Then naturally transition to experience.
 - When transitioning, say something like "Great intro! Let's dive into your past experience now."

If stage is "experience":
 - Ask about their most recent or most relevant experience for {role}.
 - If they mention MULTIPLE experiences, rank them by relevance to {role} and start with the most relevant.
 - For each experience drill deep: problem/goal, their role, technical decisions, challenges, impact/metrics.
 - After covering one experience sufficiently, ask about the next one.
 - After enough depth (6-10 exchanges), wrap up naturally.

If stage is "done":
 - Provide exactly 3 bullets: Strength, Gap, Next step — referencing their actual answers.
 - Thank them warmly.

SIGNAL (required):
End every reply with exactly one signal on its own line:
<<<STAY>>>                 — remain in current stage
<<<MOVE_TO_EXPERIENCE>>>   — transition from intro to experience
<<<MOVE_TO_DONE>>>         — transition from experience to done (wrap-up)

You MUST include exactly one signal at the end of every reply."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SIGNAL_RE = re.compile(r"<<<(STAY|MOVE_TO_EXPERIENCE|MOVE_TO_DONE)>>>")


def _parse(raw: str) -> tuple:
    m = _SIGNAL_RE.search(raw)
    signal = m.group(1) if m else "STAY"
    clean = _SIGNAL_RE.sub("", raw).strip()
    # Remove stray markdown formatting the model sometimes adds
    clean = re.sub(r"^\*\*Taylor:\*\*\s*", "", clean)
    clean = re.sub(r"^Taylor:\s*", "", clean)
    return clean, signal


def _count(history: list, role: str, stage: str) -> int:
    return sum(1 for m in history if m.get("role") == role and m.get("stage") == stage)


def _build_contents(history: list) -> list:
    """Convert DB history to Gemini alternating user/model content list."""
    raw = []
    for msg in history:
        r = msg.get("role", "system")
        text = (msg.get("text") or "").strip()
        if not text or r == "system":
            continue
        gemini_role = "model" if r == "agent" else "user"
        # Strip leftover signals from model text
        if gemini_role == "model":
            text = _SIGNAL_RE.sub("", text).strip()
            text = re.sub(r"^\*\*Taylor:\*\*\s*", "", text)
            text = re.sub(r"^Taylor:\s*", "", text)
        if not text:
            continue
        raw.append({"role": gemini_role, "text": text})

    # Merge consecutive same-role (Gemini strict alternation requirement)
    merged = []
    for m in raw:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["text"] += "\n" + m["text"]
        else:
            merged.append(dict(m))

    # Convert to Gemini content format
    contents = []
    for m in merged:
        contents.append(
            types.Content(
                role=m["role"],
                parts=[types.Part.from_text(text=m["text"])],
            )
        )

    return contents


# ---------------------------------------------------------------------------
# Main engine function
# ---------------------------------------------------------------------------
def run_engine(
    *,
    session_id: str,
    candidate_name: str,
    role_name: str,
    event_type: str,       # "start" | "user_turn" | "timeout"
    user_text: str,
    history: List[Dict[str, Any]],
    session_stage: str,     # "intro" | "experience" | "done"
) -> Dict[str, Any]:
    """
    Single synchronous call: takes session state → calls Gemini → returns result.
    Returns: {assistant_text: str, next_stage: str, done: bool}
    """
    stage = session_stage

    # Already done
    if stage == "done":
        return {
            "assistant_text": "This session is already complete. Please create a new session.",
            "next_stage": "done",
            "done": True,
        }

    # Timeout → force transition
    if event_type == "timeout":
        if stage == "intro":
            stage = "experience"
        elif stage == "experience":
            stage = "done"

    # Guard-rail transitions by count
    intro_n = _count(history, "agent", "intro")
    exp_n = _count(history, "agent", "experience")
    if stage == "intro" and intro_n >= MAX_INTRO_TURNS:
        stage = "experience"
    if stage == "experience" and exp_n >= MAX_EXP_TURNS:
        stage = "done"

    # Build Gemini request
    system = _system_prompt(candidate_name, role_name, stage)
    contents = _build_contents(history)

    # Current user message
    if event_type == "start":
        user_msg = "[The interview is starting. Greet the candidate and ask your first intro question.]"
    elif event_type == "timeout" and session_stage != stage:
        user_msg = f"[Due to inactivity, transition smoothly to the {stage} stage now.]"
    elif user_text:
        user_msg = user_text
    else:
        user_msg = "[Continue the interview.]"

    # Append user message (merge if last is also user)
    user_content = types.Content(
        role="user", parts=[types.Part.from_text(text=user_msg)]
    )
    if contents and contents[-1].role == "user":
        # Merge into last user content
        existing_text = contents[-1].parts[0].text or ""
        contents[-1] = types.Content(
            role="user",
            parts=[types.Part.from_text(text=existing_text + "\n" + user_msg)],
        )
    else:
        contents.append(user_content)

    # Ensure first message is user (Gemini requirement)
    if contents and contents[0].role == "model":
        contents.insert(
            0,
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="[Interview begins]")],
            ),
        )

    # Call Gemini (with model fallback for rate limits)
    raw_text = ""
    t0 = time.time()
    last_err = None
    for model_name in MODEL_FALLBACKS:
        try:
            response = _client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=TEMPERATURE,
                    max_output_tokens=MAX_TOKENS,
                ),
            )
            raw_text = response.text or ""
            elapsed = time.time() - t0
            print(f"[engine] OK {model_name} in {elapsed:.2f}s | stage={stage} | len={len(raw_text)}")
            last_err = None
            break
        except Exception as e:
            last_err = e
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                print(f"[engine] {model_name} rate-limited, trying next model...")
                continue
            else:
                print(f"[engine] {model_name} error: {e}")
                break
    if last_err:
        elapsed = time.time() - t0
        print(f"[engine] Gemini FAILED in {elapsed:.2f}s: {last_err}")
        traceback.print_exc()
        # Fallback responses
        if stage == "intro":
            raw_text = (
                f"Hi {candidate_name}! Welcome — I'm excited to chat with you about "
                f"the {role_name} position. Could you kick things off by telling me "
                f"a bit about yourself? <<<STAY>>>"
            )
        elif stage == "experience":
            raw_text = (
                "I'd love to hear about a project you've worked on recently. "
                "What was the problem you were solving, and what was your role? <<<STAY>>>"
            )
        else:
            raw_text = (
                "Thanks for a great conversation!\n\n"
                "• Strength: You communicated your ideas clearly.\n"
                "• Gap: Try to include more concrete metrics and impact.\n"
                "• Next step: Practice the STAR method for telling project stories.\n\n"
                "Best of luck — you've got this! <<<MOVE_TO_DONE>>>"
            )

    # Parse
    reply, signal = _parse(raw_text)

    if not reply or len(reply) < 3:
        reply = "Could you tell me more about that?" if stage == "experience" else "Please go on."

    # Apply signal
    next_stage = stage
    done = False
    if signal == "MOVE_TO_EXPERIENCE" and stage == "intro":
        next_stage = "experience"
    elif signal == "MOVE_TO_DONE":
        next_stage = "done"
        done = True

    if stage == "done":
        next_stage = "done"
        done = True

    return {"assistant_text": reply, "next_stage": next_stage, "done": done}