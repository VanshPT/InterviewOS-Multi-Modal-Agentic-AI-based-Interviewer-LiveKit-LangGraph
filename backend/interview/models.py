import uuid
from django.db import models


class InterviewSession(models.Model):
    class Status(models.TextChoices):
        CREATED = "created", "Created"
        RUNNING = "running", "Running"
        ENDED = "ended", "Ended"
        ERROR = "error", "Error"

    class Stage(models.TextChoices):
        INTRO = "intro", "Intro"
        EXPERIENCE = "experience", "Experience"
        DONE = "done", "Done"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # LiveKit room for this interview
    room_name = models.CharField(max_length=128, unique=True)

    candidate_name = models.CharField(max_length=128, default="Candidate")
    role = models.CharField(max_length=256, default="AI Algorithm Engineer Intern")

    status = models.CharField(max_length=32, choices=Status.choices, default=Status.CREATED)
    stage = models.CharField(max_length=32, choices=Stage.choices, default=Stage.INTRO)

    # --- Phase 2 timing fields for fallback ---
    stage_started_at = models.DateTimeField(null=True, blank=True)
    last_turn_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.candidate_name} | {self.room_name} | {self.stage}"


class InterviewMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        AGENT = "agent", "Agent"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(InterviewSession, on_delete=models.CASCADE, related_name="messages")

    role = models.CharField(max_length=16, choices=Role.choices)
    stage = models.CharField(max_length=32, default="intro")

    text = models.TextField()
    is_final = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["session", "created_at"]),
        ]

    def __str__(self):
        return f"{self.role} | {self.stage} | {self.created_at}"