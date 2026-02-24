import json
import uuid
from django.conf import settings
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.template import loader
from django.template import TemplateDoesNotExist
from django.utils import timezone

from .models import InterviewSession, InterviewMessage
from .engine import run_engine
from livekit.api import AccessToken, VideoGrants
from livekit.api import RoomAgentDispatch, RoomConfiguration

def health(request):
    return JsonResponse({"ok": True})


def demo(request):
    try:
        template = loader.get_template("interview/demo.html")
        return HttpResponse(template.render({}, request))
    except TemplateDoesNotExist:
        return HttpResponse(
            "Demo UI not added yet. API ready:\n"
            "- POST /api/interview/sessions/create/\n"
            "- POST /api/interview/engine/next_turn/   (requires X-INGEST-SECRET)\n"
            "- POST /api/interview/ui/next_turn/       (DEBUG-only, for browser UI)\n"
            "- GET  /api/interview/sessions/<id>/messages/\n",
            content_type="text/plain",
        )


@csrf_exempt
@require_http_methods(["POST"])
def create_session(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    candidate_name = (payload.get("candidate_name") or "Candidate").strip()
    role = (payload.get("role") or "AI Algorithm Engineer Intern").strip()
    room_name = f"interview-{uuid.uuid4().hex[:10]}"

    now = timezone.now()

    session = InterviewSession.objects.create(
        room_name=room_name,
        candidate_name=candidate_name,
        role=role,
        status=InterviewSession.Status.CREATED,
        stage=InterviewSession.Stage.INTRO,
        stage_started_at=now,
        last_turn_at=now,
    )

    InterviewMessage.objects.create(
        session=session,
        role=InterviewMessage.Role.SYSTEM,
        stage=session.stage,
        text="Session created.",
        is_final=True,
        meta={},
    )

    return JsonResponse(
        {
            "session_id": str(session.id),
            "room_name": session.room_name,
            "candidate_name": session.candidate_name,
            "role": session.role,
            "status": session.status,
            "stage": session.stage,
        },
        status=201,
    )


@require_http_methods(["GET"])
def list_messages(request, session_id):
    try:
        session = InterviewSession.objects.get(id=session_id)
    except InterviewSession.DoesNotExist:
        return JsonResponse({"error": "Unknown session_id"}, status=404)

    msgs = (
        session.messages
        .order_by("created_at")
        .values("created_at", "role", "stage", "text", "is_final", "meta")
    )

    return JsonResponse(
        {
            "session_id": str(session.id),
            "status": session.status,
            "stage": session.stage,
            "stage_started_at": session.stage_started_at.isoformat() if session.stage_started_at else None,
            "last_turn_at": session.last_turn_at.isoformat() if session.last_turn_at else None,
            "messages": list(msgs),
        }
    )


def _handle_next_turn(request, *, require_secret: bool):
    """
    Shared handler for:
      - next_turn (protected, for LiveKit Builder)
      - ui_next_turn (DEBUG-only, for browser UI)
    """
    if require_secret:
        if request.headers.get("X-INGEST-SECRET", "") != (settings.INGEST_SECRET or ""):
            return HttpResponseForbidden("Bad ingest secret")
    else:
        # UI endpoint should only work in DEBUG mode
        if not settings.DEBUG:
            return HttpResponseForbidden("UI endpoint disabled when DEBUG=False")

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    session_id = payload.get("session_id")
    event_type = (payload.get("event_type") or "user_turn").strip()  # start|user_turn
    user_text = (payload.get("user_text") or "").strip()
    event_id = (payload.get("event_id") or "").strip()

    if not session_id:
        return JsonResponse({"error": "session_id is required"}, status=400)

    try:
        session = InterviewSession.objects.get(id=session_id)
    except InterviewSession.DoesNotExist:
        return JsonResponse({"error": "Unknown session_id"}, status=404)

    now = timezone.now()

    # mark running on first interaction
    if session.status == InterviewSession.Status.CREATED:
        session.status = InterviewSession.Status.RUNNING
        session.last_turn_at = now
        if not session.stage_started_at:
            session.stage_started_at = now
        session.save(update_fields=["status", "last_turn_at", "stage_started_at", "updated_at"])

    # idempotency
    if event_id:
        exists = InterviewMessage.objects.filter(session=session, meta__event_id=event_id).exists()
        if exists:
            last_agent = (
                InterviewMessage.objects
                .filter(session=session, role=InterviewMessage.Role.AGENT)
                .order_by("-created_at")
                .first()
            )
            return JsonResponse({
                "assistant_text": last_agent.text if last_agent else "",
                "stage": session.stage,
                "done": session.stage == "done",
            })

    # validate user_turn text
    if event_type == "user_turn" and not user_text:
        return JsonResponse({"error": "user_text required for event_type=user_turn"}, status=400)

    # store user msg (exactly one row per user answer)
    if event_type == "user_turn":
        InterviewMessage.objects.create(
            session=session,
            role=InterviewMessage.Role.USER,
            stage=session.stage,
            text=user_text,
            is_final=True,
            meta={"event_id": event_id} if event_id else {},
        )
        session.last_turn_at = now
        session.save(update_fields=["last_turn_at", "updated_at"])

    # time-based fallback: force "timeout" into engine
    stage_started = session.stage_started_at or session.created_at
    elapsed_s = (now - stage_started).total_seconds()

    engine_event_type = event_type
    if session.stage == "intro" and elapsed_s >= settings.INTRO_TIMEOUT_S:
        engine_event_type = "timeout"
    if session.stage == "experience" and elapsed_s >= settings.EXPERIENCE_TIMEOUT_S:
        engine_event_type = "timeout"

    # load full history
    history = list(session.messages.order_by("created_at").values("role", "stage", "text"))

    # run Gemini engine
    out = run_engine(
        session_id=str(session.id),
        candidate_name=session.candidate_name,
        role_name=session.role,
        event_type=engine_event_type,
        user_text=user_text,
        history=history,
        session_stage=session.stage,
    )

    assistant_text = out["assistant_text"]
    next_stage = out["next_stage"]
    done = bool(out["done"])

    # store agent msg
    InterviewMessage.objects.create(
        session=session,
        role=InterviewMessage.Role.AGENT,
        stage=next_stage,
        text=assistant_text,
        is_final=True,
        meta={"event_id": event_id, "engine_event_type": engine_event_type} if event_id else {"engine_event_type": engine_event_type},
    )

    # update stage timing if stage changed
    stage_changed = (next_stage != session.stage)
    session.stage = next_stage
    if stage_changed:
        session.stage_started_at = now

    if done:
        session.status = InterviewSession.Status.ENDED
        session.ended_at = now
        session.save(update_fields=["stage", "stage_started_at", "status", "ended_at", "updated_at"])
    else:
        session.save(update_fields=["stage", "stage_started_at", "updated_at"])

    return JsonResponse({"assistant_text": assistant_text, "stage": next_stage, "done": done})


@csrf_exempt
@require_http_methods(["POST"])
def next_turn(request):
    # protected endpoint (for LiveKit Builder)
    return _handle_next_turn(request, require_secret=True)


@csrf_exempt
@require_http_methods(["POST"])
def ui_next_turn(request):
    # DEBUG-only endpoint (for browser UI)
    return _handle_next_turn(request, require_secret=False)


@csrf_exempt
@require_http_methods(["POST"])
def livekit_token(request):
    payload = json.loads(request.body.decode("utf-8") or "{}")
    session_id = (payload.get("session_id") or "").strip()
    if not session_id:
        return JsonResponse({"error": "session_id is required"}, status=400)

    try:
        session = InterviewSession.objects.get(id=session_id)
    except InterviewSession.DoesNotExist:
        return JsonResponse({"error": "Unknown session_id"}, status=404)

    # identity must be unique per participant
    identity = f"cand-{session.id}"
    agent_name = getattr(settings, "AGENT_NAME", "Taylor-23fe")

    job_meta = json.dumps({
    "session_id": str(session.id),
    "candidate_name": session.candidate_name,
    "role": session.role,
     })

    token = (
        AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(session.candidate_name)
        .with_metadata(json.dumps({"session_id": str(session.id)}))
        .with_grants(
            VideoGrants(
                room_join=True,
                room=session.room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_room_config(
            RoomConfiguration(
                agents=[RoomAgentDispatch(agent_name=agent_name, metadata=job_meta)]
            )
        )
        .to_jwt()
    )

    return JsonResponse({
        "url": settings.LIVEKIT_URL,
        "room_name": session.room_name,
        "token": token,
        "session_id": str(session.id),
    })