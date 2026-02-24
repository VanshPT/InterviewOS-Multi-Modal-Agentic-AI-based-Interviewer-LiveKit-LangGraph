from django.contrib import admin
from .models import InterviewSession, InterviewMessage

admin.site.register(InterviewSession)
admin.site.register(InterviewMessage)