from django.urls import path
from . import views

urlpatterns = [
    # Health check
    path('health/', views.HealthCheckView.as_view(), name='health'),

    # Call management
    path('make-call/', views.MakeCallView.as_view(), name='make_call'),          # Outbound
    path('inbound/', views.InboundCallView.as_view(), name='inbound'),                # Inbound
    path('twiml/', views.TwiMLView.as_view(), name='twiml'),                      # TwiML for outbound
    path('call-status/', views.CallStatusView.as_view(), name='call_status'),    # Twilio status webhook

    # Call logs & history
    path('call-history/', views.CallHistoryView.as_view(), name='call_history'),
    path('call-detail/<str:call_sid>/', views.CallDetailView.as_view(), name='call_detail'),

    # Test mode (no Twilio needed)
    path('test/', views.test_page, name='test_page'),               # Browser chat test (HTML)
    path('test-chat/', views.TestChatView.as_view(), name='test_chat'),          # Text → AI text
    path('test-voice/', views.TestVoiceView.as_view(), name='test_voice'),       # Text → AI text + audio
    path('voice-test/', views.voice_call_page, name='voice_call'),  # Browser voice call (HTML)
]
