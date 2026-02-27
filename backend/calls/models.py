from django.db import models
import uuid


class CallSession(models.Model):
    """One record per phone call — tracks the full lifecycle."""

    STATUS_CHOICES = [
        ('initiated', 'Initiated'),
        ('ringing', 'Ringing'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('no_answer', 'No Answer'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call_sid = models.CharField(max_length=64, unique=True, db_index=True, help_text="Twilio Call SID")
    stream_sid = models.CharField(max_length=64, blank=True, null=True, help_text="Twilio Stream SID")
    from_number = models.CharField(max_length=20)
    to_number = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='initiated')

    # Timing
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(blank=True, null=True)
    duration_seconds = models.IntegerField(blank=True, null=True)

    # External API context integration
    context_url = models.URLField(blank=True, null=True, help_text="External API to fetch caller context from")
    context_headers = models.JSONField(blank=True, null=True, help_text="Headers for the context API call")
    context_data = models.JSONField(blank=True, null=True, help_text="Data fetched from the context API")
    system_prompt = models.TextField(
        default="You are a helpful, brief, and friendly AI phone assistant. Always speak conversationally.",
        help_text="System prompt for the AI during this call"
    )

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"Call {self.call_sid} ({self.to_number}) - {self.status}"


class ConversationMessage(models.Model):
    """Each speech turn in the call — user's words and AI's replies."""

    ROLE_CHOICES = [
        ('system', 'System'),
        ('user', 'User'),
        ('assistant', 'Assistant'),
    ]

    session = models.ForeignKey(CallSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"[{self.role}] {self.content[:60]}"


class CallEvent(models.Model):
    """Lifecycle events for debugging and analytics."""

    EVENT_TYPES = [
        ('call_initiated', 'Call Initiated'),
        ('call_started', 'Call Started'),
        ('context_fetched', 'Context Fetched'),
        ('transcription', 'Transcription'),
        ('ai_response', 'AI Response'),
        ('tts_sent', 'TTS Sent'),
        ('call_ended', 'Call Ended'),
        ('error', 'Error'),
    ]

    session = models.ForeignKey(CallSession, on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)
    detail = models.TextField(blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.event_type} @ {self.timestamp}"
