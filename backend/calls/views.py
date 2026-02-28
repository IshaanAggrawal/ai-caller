from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.decorators import api_view
from twilio.rest import Client
import os
import json
import base64
import certifi
from datetime import datetime, timezone

from .models import CallSession, CallEvent
from .serializers import (
    CallSessionSerializer,
    CallSessionListSerializer,
    MakeCallRequestSerializer,
    TestChatRequestSerializer
)

# Fix for broken SSL cert on Windows
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
os.environ['CURL_CA_BUNDLE'] = certifi.where()


# ------------------------------------------------------------------
# Health Check
# ------------------------------------------------------------------

class HealthCheckView(APIView):
    """GET /calls/health/ — Service health check."""
    
    def get(self, request):
        return Response({
            'status': 'ok',
            'service': 'ai-voice-caller',
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })


# ------------------------------------------------------------------
# Outbound Calls
# ------------------------------------------------------------------

class MakeCallView(APIView):
    """
    POST /calls/make-call/ — Initiate an outbound call via Twilio.
    """
    
    def post(self, request):
        serializer = MakeCallRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        data = serializer.validated_data
        to_phone_number = data.get('to')

        account_sid = os.environ['TWILIO_ACCOUNT_SID']
        auth_token = os.environ['TWILIO_AUTH_TOKEN']
        from_phone_number = os.environ['TWILIO_PHONE_NUMBER']
        domain = os.environ.get('DOMAIN')

        if not domain:
            return Response({'error': 'Missing DOMAIN in .env'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            client = Client(account_sid, auth_token)

            call = client.calls.create(
                url=f"https://{domain}/calls/twiml/",
                to=to_phone_number,
                from_=from_phone_number,
                status_callback=f"https://{domain}/calls/call-status/",
                status_callback_event=['initiated', 'ringing', 'answered', 'completed'],
                status_callback_method='POST',
            )

            # Create CallSession in DB
            system_prompt = data.get('system_prompt') or CallSession._meta.get_field('system_prompt').default
            
            session = CallSession.objects.create(
                call_sid=call.sid,
                from_number=from_phone_number,
                to_number=to_phone_number,
                status='initiated',
                system_prompt=system_prompt,
                context_url=data.get('context_url'),
                context_headers=data.get('context_headers'),
            )

            CallEvent.objects.create(
                session=session,
                event_type='call_initiated',
                detail=f"Outbound call to {to_phone_number}, SID={call.sid}"
            )

            return Response({
                'message': 'Call initiated',
                'call_sid': call.sid,
                'session_id': str(session.id),
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ------------------------------------------------------------------
# Inbound Calls
# ------------------------------------------------------------------

class InboundCallView(APIView):
    """
    POST /calls/inbound/ — Handle incoming calls to the Twilio number.
    Returns standard HttpResponse because Twilio expects raw XML, not JSON.
    """
    
    def post(self, request):
        call_sid = request.POST.get('CallSid', '')
        from_number = request.POST.get('From', 'unknown')
        to_number = request.POST.get('To', 'unknown')
        domain = os.environ.get('DOMAIN')

        if not domain:
            return HttpResponse("Missing DOMAIN in environment.", status=500)

        # Create a CallSession for the inbound call
        session, created = CallSession.objects.get_or_create(
            call_sid=call_sid,
            defaults={
                'from_number': from_number,
                'to_number': to_number,
                'status': 'ringing',
            }
        )

        if created:
            CallEvent.objects.create(
                session=session,
                event_type='call_initiated',
                detail=f"Inbound call from {from_number}, SID={call_sid}"
            )

        # Return TwiML to connect to our WebSocket
        response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{domain}/media-stream" />
    </Connect>
</Response>"""
        return HttpResponse(response, content_type='text/xml')


# ------------------------------------------------------------------
# TwiML (for outbound calls)
# ------------------------------------------------------------------

class TwiMLView(APIView):
    """
    POST /calls/twiml/ — Twilio requests this when the callee answers an outbound call.
    Returns TwiML to connect the call to our Django Channels WebSocket.
    """
    
    def post(self, request):
        domain = os.environ.get('DOMAIN')
        if not domain:
            return HttpResponse("Missing DOMAIN in environment.", status=500)

        response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{domain}/media-stream" />
    </Connect>
</Response>"""
        return HttpResponse(response, content_type='text/xml')


# ------------------------------------------------------------------
# Call Status Webhook
# ------------------------------------------------------------------

class CallStatusView(APIView):
    """
    POST /calls/call-status/ — Twilio sends real-time call status updates here.
    """
    
    def post(self, request):
        call_sid = request.POST.get('CallSid', '')
        call_status_value = request.POST.get('CallStatus', '')
        duration = request.POST.get('CallDuration')

        # Map Twilio status to our model status
        status_map = {
            'initiated': 'initiated',
            'ringing': 'ringing',
            'in-progress': 'in_progress',
            'completed': 'completed',
            'failed': 'failed',
            'busy': 'failed',
            'no-answer': 'no_answer',
            'canceled': 'failed',
        }

        mapped_status = status_map.get(call_status_value, call_status_value)

        try:
            session = CallSession.objects.get(call_sid=call_sid)
            session.status = mapped_status

            if call_status_value in ('completed', 'failed', 'busy', 'no-answer', 'canceled'):
                session.ended_at = datetime.now(timezone.utc)
                if duration:
                    session.duration_seconds = int(duration)

            session.save()

            CallEvent.objects.create(
                session=session,
                event_type='call_ended' if mapped_status in ('completed', 'failed', 'no_answer') else 'call_started',
                detail=f"Twilio status: {call_status_value}"
            )

        except CallSession.DoesNotExist:
            pass  # Call might not have been tracked

        return Response({'status': 'received'})


# ------------------------------------------------------------------
# Call History & Detail APIs
# ------------------------------------------------------------------

class CallHistoryView(generics.ListAPIView):
    """GET /calls/call-history/ — Paginated list of past calls."""
    queryset = CallSession.objects.all().order_by('-started_at')
    serializer_class = CallSessionListSerializer
    
    # Custom pagination logic to mimic previous response structure
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        
        page = int(request.query_params.get('page', 1))
        per_page = int(request.query_params.get('per_page', 20))
        offset = (page - 1) * per_page
        
        paginated_queryset = queryset[offset:offset + per_page]
        total = queryset.count()
        
        serializer = self.get_serializer(paginated_queryset, many=True)
        return Response({
            'total': total,
            'page': page,
            'per_page': per_page,
            'results': serializer.data,
        })


class CallDetailView(generics.RetrieveAPIView):
    """GET /calls/call-detail/<call_sid>/ — Full transcript and events for a call."""
    queryset = CallSession.objects.all()
    serializer_class = CallSessionSerializer
    lookup_field = 'call_sid'


# ------------------------------------------------------------------
# Test Mode — No Twilio needed, test AI pipeline locally
# ------------------------------------------------------------------

# In-memory conversation for test mode
_test_conversations = {}


class TestChatView(APIView):
    """
    POST /calls/test-chat/ — Test the LLM without Twilio.
    """
    
    def post(self, request):
        serializer = TestChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        data = serializer.validated_data
        from groq import Groq

        message = data.get('message')
        session_id = data.get('session_id')
        system_prompt = data.get('system_prompt') or 'You are a helpful, brief, and friendly AI phone assistant. Always speak conversationally.'

        # Get or create conversation
        if session_id not in _test_conversations:
            _test_conversations[session_id] = [{"role": "system", "content": system_prompt}]

        messages = _test_conversations[session_id]
        messages.append({"role": "user", "content": message})

        try:
            client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
            completion = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=messages,
                temperature=0.6,
                max_tokens=150,
            )
            ai_response = completion.choices[0].message.content
            messages.append({"role": "assistant", "content": ai_response})

            return Response({
                'response': ai_response,
                'session_id': session_id,
                'turn': len([m for m in messages if m['role'] == 'user']),
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TestVoiceView(APIView):
    """
    POST /calls/test-voice/ — Test the full pipeline: text → LLM → ElevenLabs audio.
    """
    
    def post(self, request):
        serializer = TestChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        data = serializer.validated_data
        from groq import Groq
        from elevenlabs import ElevenLabs

        message = data.get('message')
        session_id = data.get('session_id')
        system_prompt = data.get('system_prompt') or 'You are a helpful, brief, and friendly AI phone assistant. Always speak conversationally.'

        # Conversation memory
        if session_id not in _test_conversations:
            _test_conversations[session_id] = [{"role": "system", "content": system_prompt}]

        messages = _test_conversations[session_id]
        messages.append({"role": "user", "content": message})

        # Step 1: Groq LLM
        try:
            groq = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
            completion = groq.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=messages,
                temperature=0.6,
                max_tokens=150,
            )
            ai_response = completion.choices[0].message.content
            messages.append({"role": "assistant", "content": ai_response})
        except Exception as e:
            return Response({'error': f'Groq error: {e}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Step 2: ElevenLabs TTS
        audio_b64 = None
        try:
            el_client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY", ""))
            voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
            model_id = os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2")

            audio_gen = el_client.text_to_speech.convert(
                voice_id=voice_id,
                text=ai_response,
                model_id=model_id,
                output_format="mp3_44100_128",  # mp3 for browser playback
            )
            audio_bytes = b"".join(audio_gen)
            audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
        except Exception as e:
            # Return text even if TTS fails
            return Response({
                'response': ai_response,
                'audio': None,
                'tts_error': str(e),
                'session_id': session_id,
            })

        return Response({
            'response': ai_response,
            'audio': audio_b64,
            'audio_format': 'mp3',
            'session_id': session_id,
        })


def test_page(request):
    """
    GET /calls/test/ — Browser test page.
    Type a message, hear the AI respond through your speakers.
    """
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Voice Caller - Test</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0f0f23;
    color: #e0e0e0;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }
  header {
    background: linear-gradient(135deg, #1a1a3e, #2d1b69);
    padding: 20px 24px;
    border-bottom: 1px solid #333;
  }
  header h1 { font-size: 20px; color: #fff; }
  header p { font-size: 13px; color: #888; margin-top: 4px; }
  #chat {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .msg {
    max-width: 75%;
    padding: 12px 16px;
    border-radius: 16px;
    font-size: 14px;
    line-height: 1.5;
    animation: fadeIn 0.3s ease;
  }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; } }
  .msg.user {
    align-self: flex-end;
    background: #3b82f6;
    color: #fff;
    border-bottom-right-radius: 4px;
  }
  .msg.ai {
    align-self: flex-start;
    background: #1e1e3f;
    border: 1px solid #333;
    border-bottom-left-radius: 4px;
  }
  .msg.ai .speaker { cursor: pointer; margin-left: 8px; opacity: 0.6; }
  .msg.ai .speaker:hover { opacity: 1; }
  .msg.system {
    align-self: center;
    background: transparent;
    color: #666;
    font-size: 12px;
  }
  .typing {
    align-self: flex-start;
    color: #888;
    font-size: 13px;
    padding: 8px 16px;
  }
  .typing::after {
    content: '';
    animation: dots 1.5s infinite;
  }
  @keyframes dots {
    0% { content: '.'; }
    33% { content: '..'; }
    66% { content: '...'; }
  }
  #input-area {
    padding: 16px 20px;
    background: #1a1a2e;
    border-top: 1px solid #333;
    display: flex;
    gap: 10px;
  }
  #input-area input {
    flex: 1;
    padding: 12px 16px;
    background: #0f0f23;
    border: 1px solid #444;
    border-radius: 12px;
    color: #fff;
    font-size: 14px;
    outline: none;
  }
  #input-area input:focus { border-color: #3b82f6; }
  #input-area button {
    padding: 12px 24px;
    background: #3b82f6;
    color: #fff;
    border: none;
    border-radius: 12px;
    font-size: 14px;
    cursor: pointer;
    transition: background 0.2s;
  }
  #input-area button:hover { background: #2563eb; }
  #input-area button:disabled { background: #555; cursor: not-allowed; }
  .mode-toggle {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .mode-toggle label {
    font-size: 13px;
    color: #aaa;
    display: flex;
    align-items: center;
    gap: 4px;
    cursor: pointer;
  }
  .mode-toggle input { accent-color: #3b82f6; }
  .latency { font-size: 11px; color: #666; margin-top: 4px; }
</style>
</head>
<body>
<header>
  <h1>🎙️ AI Voice Caller — Test Mode</h1>
  <p>Test the AI pipeline locally. No Twilio needed.</p>
</header>
<div id="chat">
  <div class="msg system">Type a message below. The AI will respond with text and voice.</div>
</div>
<div id="input-area">
  <input type="text" id="msg" placeholder="Type your message..." autofocus />
  <div class="mode-toggle">
    <label><input type="checkbox" id="voice-mode" checked /> 🔊 Voice</label>
  </div>
  <button id="send" onclick="sendMessage()">Send</button>
</div>
<script>
const chat = document.getElementById('chat');
const msgInput = document.getElementById('msg');
const sendBtn = document.getElementById('send');
const voiceMode = document.getElementById('voice-mode');
let sessionId = 'test-' + Date.now();

msgInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !sendBtn.disabled) sendMessage();
});

async function sendMessage() {
  const text = msgInput.value.trim();
  if (!text) return;

  // Add user message
  addMsg('user', text);
  msgInput.value = '';
  sendBtn.disabled = true;

  // Show typing indicator
  const typing = document.createElement('div');
  typing.className = 'typing';
  typing.textContent = 'AI is thinking';
  chat.appendChild(typing);
  chat.scrollTop = chat.scrollHeight;

  const start = Date.now();
  const useVoice = voiceMode.checked;
  const endpoint = useVoice ? '/calls/test-voice/' : '/calls/test-chat/';

  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ message: text, session_id: sessionId })
    });
    const data = await res.json();
    const latency = Date.now() - start;

    typing.remove();

    if (data.error) {
      addMsg('system', '❌ Error: ' + data.error);
    } else {
      const aiText = data.response;
      const msgEl = addMsg('ai', aiText);

      // Latency info
      const lat = document.createElement('div');
      lat.className = 'latency';
      lat.textContent = `⏱ ${latency}ms`;
      if (data.tts_error) lat.textContent += ` | TTS error: ${data.tts_error}`;
      msgEl.appendChild(lat);

      // Play audio if available
      if (data.audio) {
        const audio = new Audio('data:audio/mp3;base64,' + data.audio);
        audio.play();

        // Add replay button
        const speaker = document.createElement('span');
        speaker.className = 'speaker';
        speaker.textContent = '🔊';
        speaker.title = 'Replay';
        speaker.onclick = () => new Audio('data:audio/mp3;base64,' + data.audio).play();
        msgEl.querySelector('.latency').prepend(speaker);
      }
    }
  } catch (err) {
    typing.remove();
    addMsg('system', '❌ Network error: ' + err.message);
  }

  sendBtn.disabled = false;
  msgInput.focus();
}

function addMsg(role, text) {
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  el.textContent = text;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
  return el;
}
</script>
</body>
</html>"""
    return HttpResponse(html, content_type='text/html')


def voice_call_page(request):
    """
    GET /calls/voice-test/ — Browser-based voice call.
    Speak into your mic, hear the AI respond. No Twilio needed.
    Uses Web Speech API (browser STT) + Groq LLM + ElevenLabs TTS.
    """
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Voice Call - Live Test</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0a0a1a;
    color: #e0e0e0;
    height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }
  .container {
    width: 100%;
    max-width: 500px;
    padding: 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 20px;
  }
  h1 { font-size: 22px; color: #fff; text-align: center; }
  .subtitle { font-size: 13px; color: #888; text-align: center; }

  /* Call button */
  .call-btn {
    width: 120px;
    height: 120px;
    border-radius: 50%;
    border: none;
    cursor: pointer;
    font-size: 48px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.3s ease;
    position: relative;
  }
  .call-btn.idle {
    background: linear-gradient(135deg, #22c55e, #16a34a);
    box-shadow: 0 0 30px rgba(34, 197, 94, 0.3);
  }
  .call-btn.idle:hover {
    box-shadow: 0 0 50px rgba(34, 197, 94, 0.5);
    transform: scale(1.05);
  }
  .call-btn.active {
    background: linear-gradient(135deg, #ef4444, #dc2626);
    box-shadow: 0 0 30px rgba(239, 68, 68, 0.3);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { box-shadow: 0 0 30px rgba(239, 68, 68, 0.3); }
    50% { box-shadow: 0 0 60px rgba(239, 68, 68, 0.5); }
  }

  /* Status indicator */
  .status {
    text-align: center;
    font-size: 14px;
    color: #aaa;
    min-height: 24px;
  }
  .status.listening { color: #22c55e; }
  .status.thinking { color: #f59e0b; }
  .status.speaking { color: #3b82f6; }
  .status.error { color: #ef4444; }

  /* Visualizer */
  .visualizer {
    display: flex;
    gap: 3px;
    align-items: center;
    height: 40px;
  }
  .visualizer .bar {
    width: 4px;
    background: #3b82f6;
    border-radius: 2px;
    transition: height 0.1s;
  }

  /* Transcript */
  .transcript {
    width: 100%;
    max-height: 300px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px;
  }
  .transcript .msg {
    padding: 10px 14px;
    border-radius: 12px;
    font-size: 13px;
    line-height: 1.4;
    animation: fadeIn 0.3s ease;
  }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; } }
  .transcript .msg.user {
    align-self: flex-end;
    background: #1e3a5f;
    max-width: 80%;
  }
  .transcript .msg.ai {
    align-self: flex-start;
    background: #1e1e3f;
    border: 1px solid #333;
    max-width: 80%;
  }
  .transcript .msg .meta {
    font-size: 11px;
    color: #666;
    margin-top: 4px;
  }

  /* Settings panel */
  .settings {
    width: 100%;
    background: #111;
    border-radius: 12px;
    padding: 12px;
    font-size: 12px;
  }
  .settings summary {
    cursor: pointer;
    color: #888;
    font-size: 13px;
  }
  .settings .field {
    margin-top: 8px;
  }
  .settings label {
    display: block;
    color: #888;
    margin-bottom: 4px;
  }
  .settings input, .settings select {
    width: 100%;
    padding: 6px 10px;
    background: #1a1a2e;
    border: 1px solid #333;
    border-radius: 6px;
    color: #fff;
    font-size: 12px;
  }
</style>
</head>
<body>
<div class="container">
  <h1>📞 AI Voice Call</h1>
  <p class="subtitle">Speak into your mic. The AI will respond with voice.<br>No Twilio needed.</p>

  <div class="visualizer" id="visualizer">
    <div class="bar" style="height: 4px;"></div>
    <div class="bar" style="height: 4px;"></div>
    <div class="bar" style="height: 4px;"></div>
    <div class="bar" style="height: 4px;"></div>
    <div class="bar" style="height: 4px;"></div>
    <div class="bar" style="height: 4px;"></div>
    <div class="bar" style="height: 4px;"></div>
    <div class="bar" style="height: 4px;"></div>
  </div>

  <button class="call-btn idle" id="callBtn" onclick="toggleCall()">📞</button>

  <div class="status" id="status">Tap to start call</div>

  <div class="transcript" id="transcript"></div>

  <details class="settings">
    <summary>⚙️ Settings</summary>
    <div class="field">
      <label>Language</label>
      <select id="lang">
        <option value="en-US">English (US)</option>
        <option value="en-IN">English (India)</option>
        <option value="hi-IN">Hindi</option>
      </select>
    </div>
    <div class="field">
      <label>System Prompt</label>
      <input type="text" id="sysPrompt" value="You are a helpful, brief, and friendly AI phone assistant. Always speak conversationally." />
    </div>
  </details>
</div>

<script>
let callActive = false;
let recognition = null;
let sessionId = 'voice-' + Date.now();

let ws = null;
let isProcessing = false;

// AudioContext for smooth chunk playback
let audioCtx = null;
let audioQueue = [];
let isPlaying = false;
let startTime = 0;

const callBtn = document.getElementById('callBtn');
const statusEl = document.getElementById('status');
const transcript = document.getElementById('transcript');
const bars = document.querySelectorAll('.visualizer .bar');

function initAudio() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
}

// Playbacks the decoded audio buffer
function playNextBuffer() {
  if (audioQueue.length === 0) {
    isPlaying = false;
    
    // If the queue is empty, AI finished speaking, resume listening
    if (callActive) {
      isProcessing = false;
      setStatus('Listening...', 'listening');
      bars.forEach(b => b.style.background = '#22c55e');
      startListening();
    }
    return;
  }
  
  isPlaying = true;
  const audioBuffer = audioQueue.shift();
  const source = audioCtx.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(audioCtx.destination);
  
  // To ensure seamless playback between chunks
  if (startTime < audioCtx.currentTime) {
      startTime = audioCtx.currentTime;
  }
  
  source.start(startTime);
  startTime += audioBuffer.duration;
  
  source.onended = () => {
     // Check if we need to schedule next
     if (audioCtx.currentTime >= startTime) {
         playNextBuffer();
     } else {
         // The next buffer is already scheduled to play
         setTimeout(playNextBuffer, (startTime - audioCtx.currentTime) * 1000);
     }
  };
}

// Converts base64 to ArrayBuffer and decodes it
async function queueAudioChunk(base64Audio) {
  try {
    const binaryString = window.atob(base64Audio);
    const len = binaryString.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
        bytes[i] = binaryString.charCodeAt(i);
    }
    
    // Decode MP3 bytes to raw AudioBuffer
    const audioBuffer = await audioCtx.decodeAudioData(bytes.buffer);
    audioQueue.push(audioBuffer);
    
    if (!isPlaying) {
        startTime = audioCtx.currentTime;
        playNextBuffer();
    }
  } catch (e) {
    console.error("Audio decode error:", e);
  }
}

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = 'status ' + (cls || '');
}

function addMsg(role, text, meta) {
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  el.textContent = text;
  if (meta) {
    const m = document.createElement('div');
    m.className = 'meta';
    m.textContent = meta;
    el.appendChild(m);
  }
  transcript.appendChild(el);
  transcript.scrollTop = transcript.scrollHeight;
}

function animateBars(active) {
  bars.forEach(bar => {
    if (active) {
      const h = Math.random() * 30 + 5;
      bar.style.height = h + 'px';
      bar.style.background = callActive ? '#22c55e' : '#3b82f6';
    } else {
      bar.style.height = '4px';
      bar.style.background = '#3b82f6';
    }
  });
}

let barInterval = null;

function toggleCall() {
  if (callActive) {
    endCall();
  } else {
    startCall();
  }
}

function startCall() {
  if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
    setStatus('Speech recognition not supported. Use Chrome.', 'error');
    return;
  }

  callActive = true;
  callBtn.className = 'call-btn active';
  callBtn.textContent = '📵';
  sessionId = 'voice-' + Date.now();
  
  initAudio();
  if (audioCtx.state === 'suspended') {
      audioCtx.resume();
  }

  setStatus('Call started — connecting...', 'listening');
  addMsg('ai', 'Call connected. Go ahead, I\\'m listening...', 'System');

  barInterval = setInterval(() => {
    if (callActive) animateBars(true);
  }, 150);

  // Initialize WebSocket connection to WebTestConsumer
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${window.location.host}/web-stream`);
  
  ws.onopen = () => {
    ws.send(JSON.stringify({
        event: 'start',
        system_prompt: document.getElementById('sysPrompt').value
    }));
    startListening();
  };
  
  ws.onmessage = (async (e) => {
    const data = JSON.parse(e.data);
    if (data.event === 'text_chunk') {
      setStatus('AI is speaking...', 'speaking');
      bars.forEach(b => b.style.background = '#3b82f6');
      addMsg('ai', data.text, 'Streaming chunk...');
    } else if (data.event === 'audio_chunk') {
      await queueAudioChunk(data.audio);
    } else if (data.event === 'clear') {
      // User interrupted!
      audioQueue = [];
      isPlaying = false;
      startTime = audioCtx ? audioCtx.currentTime : 0;
    } else if (data.event === 'error') {
      setStatus('Error: ' + data.message, 'error');
    }
  });
  
  ws.onclose = () => {
      if (callActive) endCall();
  };
}

function endCall() {
  callActive = false;
  callBtn.className = 'call-btn idle';
  callBtn.textContent = '📞';

  if (recognition) {
    recognition.abort();
    recognition = null;
  }
  if (ws) {
      ws.close();
      ws = null;
  }
  
  audioQueue = [];
  isPlaying = false;
  if (audioCtx) {
     startTime = audioCtx.currentTime;
  }
  
  clearInterval(barInterval);
  animateBars(false);

  setStatus('Call ended', '');
  addMsg('ai', 'Call ended.', 'System');
}

function startListening() {
  if (!callActive || isProcessing) return;

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SpeechRecognition();
  recognition.lang = document.getElementById('lang').value;
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.continuous = false;

  recognition.onresult = async (event) => {
    const text = event.results[0][0].transcript;
    const confidence = event.results[0][0].confidence;

    if (!callActive) return;

    setStatus('You said: ' + text, '');
    addMsg('user', text, (confidence * 100).toFixed(0) + '% confidence');

    // Process through WebSocket
    isProcessing = true;
    setStatus('AI is thinking...', 'thinking');

    if (ws && ws.readyState === WebSocket.OPEN) {
        // Send to Django backend
        ws.send(JSON.stringify({ event: 'user_text', text: text }));
    } else {
        setStatus('WebSocket disconnected', 'error');
    }
  };

  recognition.onerror = (event) => {
    if (event.error === 'no-speech' || event.error === 'aborted') {
      // Normal — just restart listening
      if (callActive && !isProcessing) {
        setTimeout(startListening, 100);
      }
      return;
    }
    console.error('Speech error:', event.error);
    setStatus('Mic error: ' + event.error, 'error');
    if (callActive && !isProcessing) setTimeout(startListening, 2000);
  };

  recognition.onend = () => {
    // Auto-restart if call is still active and not processing
    if (callActive && !isProcessing) {
      setTimeout(startListening, 100);
    }
  };

  try {
    recognition.start();
    setStatus('Listening...', 'listening');
  } catch (e) {
    if (callActive && !isProcessing) setTimeout(startListening, 500);
  }
}
</script>
</body>
</html>"""
    return HttpResponse(html, content_type='text/html')
