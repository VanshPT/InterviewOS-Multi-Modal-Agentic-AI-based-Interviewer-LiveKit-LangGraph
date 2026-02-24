from django.urls import path
from . import views

urlpatterns = [
    path("", views.demo, name="demo"),

    path("api/interview/health/", views.health, name="health"),
    path("api/interview/sessions/create/", views.create_session, name="create_session"),
    path("api/interview/sessions/<uuid:session_id>/messages/", views.list_messages, name="list_messages"),

    # Protected (LiveKit Builder should call this later)
    path("api/interview/engine/next_turn/", views.next_turn, name="next_turn"),

    # UI-only (Phase 3): works in browser without exposing secret (DEBUG-only)
    path("api/interview/ui/next_turn/", views.ui_next_turn, name="ui_next_turn"),
    path("api/interview/livekit/token/", views.livekit_token, name="livekit_token"),
]