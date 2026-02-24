import asyncio
import json
import logging
import os
import uuid
from typing import Optional

import aiohttp
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    ToolError,
    cli,
    inference,
    llm,
    room_io,
    utils,
)
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("jobnova-router")
logging.basicConfig(level=logging.INFO)

# Load agent env
load_dotenv(".env.local")

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
INGEST_SECRET = os.getenv("INGEST_SECRET", "")
AGENT_NAME = os.getenv("AGENT_NAME", "Taylor-23fe")


async def call_engine(session_id: str, event_type: str, user_text: str = "") -> dict:
    """
    Calls Django /api/interview/engine/next_turn/ and returns parsed JSON.
    """
    url = f"{BACKEND_BASE_URL}/api/interview/engine/next_turn/"
    headers = {
        "Content-Type": "application/json",
        "X-INGEST-SECRET": INGEST_SECRET,
    }
    payload = {
        "session_id": session_id,
        "event_type": event_type,
        "user_text": user_text or "",
        "event_id": str(uuid.uuid4()),
    }

    session = utils.http_context.http_session()
    timeout = aiohttp.ClientTimeout(total=30)
    async with session.post(url, timeout=timeout, headers=headers, json=payload) as resp:
        txt = await resp.text()
        if resp.status >= 400:
            raise ToolError(f"engine HTTP {resp.status}: {txt[:200]}")
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            raise ToolError(f"engine returned non-JSON: {txt[:200]}")


class RouterAgent(Agent):
    """
    Thin router: never improvises. Always speaks assistant_text from backend.
    """
    def __init__(self, metadata: str) -> None:
        super().__init__(instructions="You are a thin router. Do not improvise.")
        self.meta_raw = metadata or "{}"
        self.session_id: Optional[str] = None
        self._did_start = False
        self._pending_user_text: Optional[str] = None

    def _parse_session_id(self) -> str:
        # Job metadata comes from RoomAgentDispatch.metadata (JSON string)
        try:
            meta = json.loads(self.meta_raw)
            sid = (meta.get("session_id") or "").strip()
            if not sid:
                raise ValueError("missing session_id")
            return sid
        except Exception as e:
            raise RuntimeError(f"Bad job metadata; need session_id JSON. err={e}")

    async def on_enter(self):
        # Called when agent starts in room
        self.session_id = self._parse_session_id()

        # Start interview immediately (no waiting for user speech)
        if not self._did_start:
            self._did_start = True
            out = await call_engine(self.session_id, "start", "")
            text = (out.get("assistant_text") or "").strip()
            if text:
                await self.session.say(text, allow_interruptions=False)

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        # Save final transcript; the response will be produced by llm_node override below
        self._pending_user_text = (new_message.text_content() or "").strip()

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list,
        model_settings,
    ):
        # Replace LLM inference entirely: call backend, yield assistant_text
        if not self.session_id:
            self.session_id = self._parse_session_id()

        user_text = (self._pending_user_text or "").strip()
        self._pending_user_text = None
        if not user_text:
            return

        out = await call_engine(self.session_id, "user_turn", user_text)
        text = (out.get("assistant_text") or "").strip()
        if text:
            yield text


server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext):
    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="en"),
        # The pipeline requires an LLM to be configured so it knows to call
        # llm_node(). Our llm_node() override intercepts ALL inference and
        # routes to our Django engine instead — this LLM is never used directly.
        llm=inference.LLM(model="google/gemini-2.5-flash"),
        tts=inference.TTS(model="cartesia/sonic-3", language="en"),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # Wait 6 seconds of silence before considering user done speaking.
        # This prevents cutting off mid-sentence during natural pauses.
        min_endpointing_delay=6.0,
        preemptive_generation=False,
    )

    await session.start(
        agent=RouterAgent(metadata=ctx.job.metadata),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )


if __name__ == "__main__":
    cli.run_app(server)