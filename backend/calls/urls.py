from django.urls import path
from . import views

urlpatterns = [
    # Health check
    path('health/', views.health, name='health'),

    # Call management
    path('make-call/', views.make_call, name='make_call'),          # Outbound
    path('inbound/', views.inbound, name='inbound'),                # Inbound
    path('twiml/', views.twiml, name='twiml'),                      # TwiML for outbound
    path('call-status/', views.call_status, name='call_status'),    # Twilio status webhook

    # Call logs & history
    path('call-history/', views.call_history, name='call_history'),
    path('call-detail/<str:call_sid>/', views.call_detail, name='call_detail'),

    # Test mode (no Twilio needed)
    path('test/', views.test_page, name='test_page'),               # Browser chat test
    path('test-chat/', views.test_chat, name='test_chat'),          # Text → AI text
    path('test-voice/', views.test_voice, name='test_voice'),       # Text → AI text + audio
    path('voice-test/', views.voice_call_page, name='voice_call'),  # Browser voice call
]
