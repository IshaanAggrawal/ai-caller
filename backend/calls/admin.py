from django.contrib import admin
from .models import CallSession, ConversationMessage, CallEvent


class ConversationMessageInline(admin.TabularInline):
    model = ConversationMessage
    extra = 0
    readonly_fields = ('role', 'content', 'timestamp')


class CallEventInline(admin.TabularInline):
    model = CallEvent
    extra = 0
    readonly_fields = ('event_type', 'detail', 'timestamp')


@admin.register(CallSession)
class CallSessionAdmin(admin.ModelAdmin):
    list_display = ('call_sid', 'to_number', 'from_number', 'status', 'started_at', 'duration_seconds')
    list_filter = ('status', 'started_at')
    search_fields = ('call_sid', 'to_number', 'from_number')
    readonly_fields = ('id', 'call_sid', 'stream_sid', 'started_at', 'context_data')
    inlines = [ConversationMessageInline, CallEventInline]


@admin.register(ConversationMessage)
class ConversationMessageAdmin(admin.ModelAdmin):
    list_display = ('session', 'role', 'content_preview', 'timestamp')
    list_filter = ('role',)

    def content_preview(self, obj):
        return obj.content[:80] + '...' if len(obj.content) > 80 else obj.content


@admin.register(CallEvent)
class CallEventAdmin(admin.ModelAdmin):
    list_display = ('session', 'event_type', 'detail_preview', 'timestamp')
    list_filter = ('event_type',)

    def detail_preview(self, obj):
        return obj.detail[:80] + '...' if len(obj.detail) > 80 else obj.detail
