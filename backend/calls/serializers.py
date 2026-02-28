from rest_framework import serializers
from .models import CallSession, ConversationMessage, CallEvent


class ConversationMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversationMessage
        fields = ['role', 'content', 'timestamp']


class CallEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CallEvent
        fields = ['event_type', 'detail', 'timestamp']


class CallSessionSerializer(serializers.ModelSerializer):
    messages = ConversationMessageSerializer(many=True, read_only=True)
    events = CallEventSerializer(many=True, read_only=True)

    class Meta:
        model = CallSession
        fields = [
            'id', 'call_sid', 'from_number', 'to_number', 'status',
            'started_at', 'ended_at', 'duration_seconds',
            'system_prompt', 'context_url', 'context_data',
            'messages', 'events'
        ]


class CallSessionListSerializer(serializers.ModelSerializer):
    # Leaner serializer for the list view (excludes messages and events)
    class Meta:
        model = CallSession
        fields = [
            'id', 'call_sid', 'from_number', 'to_number', 'status',
            'started_at', 'ended_at', 'duration_seconds'
        ]


class MakeCallRequestSerializer(serializers.Serializer):
    to = serializers.CharField(required=True)
    system_prompt = serializers.CharField(required=False, allow_blank=True)
    context_url = serializers.URLField(required=False, allow_blank=True)
    context_headers = serializers.DictField(required=False)

    def validate_to(self, value):
        # Basic validation to ensure it looks like a phone number
        if not value.startswith('+'):
            raise serializers.ValidationError("Phone number must start with '+' and include country code.")
        return value


class TestChatRequestSerializer(serializers.Serializer):
    message = serializers.CharField(required=True)
    session_id = serializers.CharField(required=False, default="default")
    system_prompt = serializers.CharField(required=False, allow_blank=True)
