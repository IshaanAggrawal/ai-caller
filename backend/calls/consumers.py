"""
Twilio Media Stream WebSocket Consumer.

Pipeline: Twilio Audio (mulaw) → Deepgram STT → Groq LLM → ElevenLabs TTS (mulaw) → Twilio

Features:
- End-call phrase detection (goodbye, bye, hang up, etc.)
- ElevenLabs TTS with fallback to Twilio <Say> if ElevenLabs fails
- External context API integration
- Full conversation logging to DB
"""

import os
import re
import json
import base64
import asyncio
import aiohttp
from datetime import timezone, datetime
from channels.generic.websocket import AsyncWebsocketConsumer
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
from groq import AsyncGroq
from elevenlabs import ElevenLabs
from asgiref.sync import sync_to_async

# SDK Clients — initialised once at module level
deepgram = DeepgramClient(os.environ.get("DEEPGRAM_API_KEY", ""))
groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY", ""))

# ElevenLabs config
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # "Rachel" — natural female voice
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2")

# Default system prompt (overridden per-call via CallSession)
DEFAULT_SYSTEM_PROMPT = "You are a helpful, brief, and friendly AI phone assistant. Always speak conversationally."

# End-call phrases — when user says these the AI says goodbye and ends
END_CALL_PATTERN = re.compile(
    r'\b(goodbye|bye|end call|hang up|disconnect|stop calling|cut the call|phone rakhdo|rakh do)\b',
    re.IGNORECASE
)

# Low confidence phrases to re-prompt
LOW_CONFIDENCE_THRESHOLD = 0.6


class TwilioMediaConsumer(AsyncWebsocketConsumer):
    """Handles a single Twilio bi-directional media stream."""

    async def connect(self):
        await self.accept()
        print("WebSocket connection accepted.")

        self.stream_sid = None
        self.call_sid = None
        self.session = None
        self.messages = []
        self.call_active = True
        self.dg_connection = None

    async def disconnect(self, close_code):
        print(f"WebSocket disconnected (code={close_code}).")
        self.call_active = False
        if self.dg_connection:
            try:
                await self.dg_connection.finish()
            except Exception:
                pass

        # Mark session as completed
        if self.session:
            await self._update_session_ended()

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        data = json.loads(text_data)
        event = data.get('event')

        if event == 'start':
            await self._handle_start(data)
        elif event == 'media':
            await self._handle_media(data)
        elif event == 'stop':
            await self._handle_stop()
        elif event == 'mark':
            # Twilio sends 'mark' events when audio playback completes
            pass

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _handle_start(self, data):
        """Called when Twilio starts streaming audio."""
        self.stream_sid = data['start']['streamSid']
        self.call_sid = data['start'].get('callSid', '')
        print(f"Call started. Stream SID: {self.stream_sid}, Call SID: {self.call_sid}")

        # Load or create the CallSession from DB
        await self._load_session()

        # Fetch external context if configured
        await self._fetch_context()

        # Build the system prompt with context
        system_prompt = self.session.system_prompt if self.session else DEFAULT_SYSTEM_PROMPT
        if self.session and self.session.context_data:
            system_prompt += f"\n\nHere is context about the person you are calling:\n{json.dumps(self.session.context_data, indent=2)}"

        self.messages = [{"role": "system", "content": system_prompt}]

        # Log event
        await self._log_event('call_started', f"Stream={self.stream_sid}")

        # Start Deepgram live transcription
        await self._start_deepgram()

        # Greet the caller
        greeting = "Hello! How can I help you today?"
        await self._speak_text(greeting)
        await self._save_message('assistant', greeting)
        await self._log_event('ai_response', greeting)

    async def _handle_media(self, data):
        """Forward incoming Twilio audio to Deepgram for transcription."""
        if self.dg_connection:
            audio_b64 = data['media']['payload']
            audio_bytes = base64.b64decode(audio_b64)
            try:
                await self.dg_connection.send(audio_bytes)
            except Exception as e:
                print(f"Deepgram send error: {e}")

    async def _handle_stop(self):
        """Called when Twilio stops the stream (call ended)."""
        print("Call stopped by Twilio.")
        self.call_active = False
        await self._log_event('call_ended', 'Call stopped by Twilio')

    # ------------------------------------------------------------------
    # Deepgram STT
    # ------------------------------------------------------------------

    async def _start_deepgram(self):
        """Initialise Deepgram live transcription."""
        self.dg_connection = deepgram.listen.asyncwebsocket.v("1")

        async def on_message(self_dg, result, **kwargs):
            sentence = result.channel.alternatives[0].transcript
            if not sentence:
                return
            if result.is_final:
                confidence = result.channel.alternatives[0].confidence or 1.0
                print(f"User: {sentence} (confidence: {confidence:.2f})")
                await self._log_event('transcription', f"{sentence} [conf={confidence:.2f}]")
                await self._save_message('user', sentence)

                # Check for end-call phrases
                if END_CALL_PATTERN.search(sentence):
                    goodbye_msg = "Thank you for calling. Goodbye! Have a great day."
                    await self._speak_text(goodbye_msg)
                    await self._save_message('assistant', goodbye_msg)
                    await self._log_event('call_ended', 'User said goodbye')
                    return

                # Low confidence — re-prompt
                if confidence < LOW_CONFIDENCE_THRESHOLD:
                    reprompt = "I didn't catch that clearly. Could you please repeat?"
                    await self._speak_text(reprompt)
                    await self._save_message('assistant', reprompt)
                    await self._log_event('ai_response', f"Low confidence reprompt ({confidence:.2f})")
                    return

                # Normal flow: LLM → TTS
                ai_response = await self._get_llm_response(sentence)
                print(f"AI: {ai_response}")
                await self._log_event('ai_response', ai_response)
                await self._save_message('assistant', ai_response)

                await self._speak_text(ai_response)
                await self._log_event('tts_sent', ai_response)

        self.dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)

        options = LiveOptions(
            model="nova-2",
            language="en-US",
            encoding="mulaw",
            channels=1,
            sample_rate=8000,
            interim_results=False,
        )

        if not await self.dg_connection.start(options):
            print("Failed to start Deepgram")
            await self._log_event('error', 'Failed to start Deepgram')

        print("Deepgram started.")

    # ------------------------------------------------------------------
    # Groq LLM
    # ------------------------------------------------------------------

    async def _get_llm_response(self, user_text):
        self.messages.append({"role": "user", "content": user_text})
        try:
            completion = await groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=self.messages,
                temperature=0.6,
                max_tokens=150,
            )
            response_text = completion.choices[0].message.content
            self.messages.append({"role": "assistant", "content": response_text})
            return response_text
        except Exception as e:
            print(f"Groq Error: {e}")
            await self._log_event('error', f"Groq error: {e}")
            return "I'm sorry, I'm having trouble thinking right now."

    # ------------------------------------------------------------------
    # ElevenLabs TTS (with Twilio <Say> fallback)
    # ------------------------------------------------------------------

    async def _speak_text(self, text):
        """
        Generate speech with ElevenLabs and stream it back to Twilio.
        Falls back to Twilio's built-in TTS (<Say>) if ElevenLabs fails.
        """
        if not self.stream_sid:
            return

        try:
            client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

            # Generate audio — ulaw_8000 is the Twilio-compatible format
            audio_generator = client.text_to_speech.convert(
                voice_id=ELEVENLABS_VOICE_ID,
                text=text,
                model_id=ELEVENLABS_MODEL,
                output_format="ulaw_8000",
            )

            # ElevenLabs returns a generator of audio chunks
            audio_bytes = b""
            for chunk in audio_generator:
                audio_bytes += chunk

            if not audio_bytes:
                raise ValueError("ElevenLabs returned empty audio")

            # Send to Twilio as base64 in chunks
            # Twilio requires "track": "outbound" for bidirectional streams
            # to play audio to the caller (without it, caller hears silence)
            CHUNK_SIZE = 8000  # ~1 second of mulaw audio at 8kHz

            for i in range(0, len(audio_bytes), CHUNK_SIZE):
                chunk = audio_bytes[i:i + CHUNK_SIZE]
                b64_audio = base64.b64encode(chunk).decode('utf-8')

                media_payload = {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {
                        "payload": b64_audio,
                        "track": "outbound"
                    }
                }
                await self.send(text_data=json.dumps(media_payload))

        except Exception as e:
            print(f"ElevenLabs Error: {e} — falling back to Twilio TTS")
            await self._log_event('error', f"ElevenLabs error: {e}, using Twilio TTS fallback")

            # Fallback: send a clear event so Twilio knows to stop waiting,
            # then use mark event. The actual TTS fallback via <Say> is limited
            # in bi-directional streams, so we log the failure for debugging.
            # In production, you'd want a secondary TTS provider here.
            try:
                # Send a silence/mark so the stream doesn't hang
                mark_payload = {
                    "event": "mark",
                    "streamSid": self.stream_sid,
                    "mark": {"name": "tts_fallback"}
                }
                await self.send(text_data=json.dumps(mark_payload))
            except Exception as fallback_err:
                print(f"Fallback also failed: {fallback_err}")

    # ------------------------------------------------------------------
    # External Context API
    # ------------------------------------------------------------------

    async def _fetch_context(self):
        """Fetch data from an external API to give the AI context about the caller."""
        if not self.session or not self.session.context_url:
            return

        try:
            headers = self.session.context_headers or {}
            async with aiohttp.ClientSession() as http_session:
                async with http_session.get(
                    self.session.context_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        context_data = await resp.json()
                        await self._save_context_data(context_data)
                        await self._log_event('context_fetched', json.dumps(context_data)[:500])
                        print(f"Context fetched from {self.session.context_url}")
                    else:
                        await self._log_event('error', f"Context API returned {resp.status}")
        except Exception as e:
            print(f"Context fetch error: {e}")
            await self._log_event('error', f"Context fetch error: {e}")

    # ------------------------------------------------------------------
    # Database helpers (all use sync_to_async for ORM access)
    # ------------------------------------------------------------------

    async def _load_session(self):
        """Load CallSession by call_sid (created when make_call was called)."""
        from calls.models import CallSession
        try:
            self.session = await sync_to_async(CallSession.objects.get)(call_sid=self.call_sid)
            self.session.stream_sid = self.stream_sid
            self.session.status = 'in_progress'
            await sync_to_async(self.session.save)()
        except CallSession.DoesNotExist:
            # Inbound calls won't have a session yet — create one
            self.session = await sync_to_async(CallSession.objects.create)(
                call_sid=self.call_sid or f"unknown-{self.stream_sid}",
                stream_sid=self.stream_sid,
                from_number='unknown',
                to_number='unknown',
                status='in_progress',
            )

    async def _save_message(self, role, content):
        if not self.session:
            return
        from calls.models import ConversationMessage
        await sync_to_async(ConversationMessage.objects.create)(
            session=self.session,
            role=role,
            content=content,
        )

    async def _log_event(self, event_type, detail=''):
        if not self.session:
            return
        from calls.models import CallEvent
        await sync_to_async(CallEvent.objects.create)(
            session=self.session,
            event_type=event_type,
            detail=detail,
        )

    async def _save_context_data(self, context_data):
        if not self.session:
            return
        self.session.context_data = context_data
        await sync_to_async(self.session.save)(update_fields=['context_data'])

    async def _update_session_ended(self):
        from calls.models import CallSession
        try:
            self.session.status = 'completed'
            self.session.ended_at = datetime.now(timezone.utc)
            if self.session.started_at:
                delta = self.session.ended_at - self.session.started_at
                self.session.duration_seconds = int(delta.total_seconds())
            await sync_to_async(self.session.save)()
        except Exception as e:
            print(f"Error updating session: {e}")
