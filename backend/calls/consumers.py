"""
Twilio Media Stream WebSocket Consumer.

Pipeline: Twilio Audio (mulaw) → Deepgram STT → Groq LLM → ElevenLabs TTS (mulaw) → Twilio

Features:
- End-call phrase detection (goodbye, bye, hang up, etc.)
- ElevenLabs TTS with fallback to Twilio <Say> if ElevenLabs fails
- External context API integration
- Full conversation logging to DB
- **Low latency**: Groq streaming → ElevenLabs
- **Interruption handling**: AI stops speaking instantly if user interrupts
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
from elevenlabs.client import AsyncElevenLabs
from asgiref.sync import sync_to_async

# SDK Clients — initialised once at module level
deepgram = DeepgramClient(os.environ.get("DEEPGRAM_API_KEY", ""))
groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY", ""))
el_client = AsyncElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY", ""))

# ElevenLabs config
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # "Rachel" — natural female voice
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2_5") # Upgraded to 2.5 for lower latency

# Default system prompt (overridden per-call via CallSession)
DEFAULT_SYSTEM_PROMPT = "You are a helpful, brief, and friendly AI phone assistant. Always speak conversationally. Keep your answers short. NEVER use emojis, markdown formatting, or asterisks like *laughs*."

# End-call phrases — when user says these the AI says goodbye and ends
END_CALL_PATTERN = re.compile(
    r'\b(goodbye|bye|end call|hang up|disconnect|stop calling|cut the call|phone rakhdo|rakh do)\b',
    re.IGNORECASE
)

# Low confidence phrases to re-prompt
LOW_CONFIDENCE_THRESHOLD = 0.5


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
        
        # State tracking for interruptions and latency
        self.response_task = None
        self.interrupted = False
        self.is_ai_speaking = False
        self.ai_spoken_buffer = "" # Tracks what AI is currently saying to prevent Echo Hallucinations
        
        # Transcription Debounce Buffer
        self.transcription_buffer = []
        self.llm_debounce_task = None

    async def disconnect(self, close_code):
        print(f"WebSocket disconnected (code={close_code}).")
        self.call_active = False
        self._cancel_response_task()
        
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
            if data['mark']['name'] == 'ai_finished_speaking':
                # Only clear the flag if we haven't already started a new response
                if not self.response_task or self.response_task.done():
                    self.is_ai_speaking = False

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
        asyncio.create_task(self._save_message('assistant', greeting))
        asyncio.create_task(self._log_event('ai_response', greeting))
        
        # Fire off greeting generator as a background task
        async def greeting_gen():
            yield greeting
            
        self.response_task = asyncio.create_task(
            self._handle_ai_response(greeting_gen(), greeting)
        )

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
        self._cancel_response_task()
        await self._log_event('call_ended', 'Call stopped by Twilio')

    def _cancel_response_task(self):
        """Helper to safely cancel the current AI speaking task."""
        if self.response_task and not self.response_task.done():
            self.response_task.cancel()
            self.response_task = None
        self.is_ai_speaking = False # Force clear the flag locally just in case
        self.ai_spoken_buffer = "" # Clear echo buffer

    async def _clear_twilio_buffer(self):
        """Send a 'clear' event to instantly stop Twilio from playing queued audio."""
        if self.call_active and self.stream_sid:
            try:
                clear_payload = {
                    "event": "clear",
                    "streamSid": self.stream_sid
                }
                await self.send(text_data=json.dumps(clear_payload))
                self.is_ai_speaking = False
                print("Sent clear to Twilio.")
            except Exception as e:
                print(f"Error sending clear: {e}")

    # ------------------------------------------------------------------
    # Deepgram STT
    # ------------------------------------------------------------------

    async def _start_deepgram(self):
        """Initialise Deepgram live transcription."""
        self.dg_connection = deepgram.listen.asyncwebsocket.v("1")

        async def on_message(self_dg, result, **kwargs):
            # Sometimes deepgram can emit empty interim transcripts, ignore them
            if not result.channel.alternatives:
                return

            sentence = result.channel.alternatives[0].transcript.strip()
            
            # Identify if this utterance is substantial enough to be considered a real interruption
            is_substantial = len(sentence) > 3 or len(sentence.split()) >= 2
            
            # --- ANTI-ECHO HALLUCINATION ENGINE ---
            # If the phone is on speakerphone, Deepgram might transcribe the AI's own voice.
            # We compare the Deepgram transcript to what the AI is currently speaking.
            # If it's highly similar, we drop it entirely.
            if self.is_ai_speaking and self.ai_spoken_buffer:
                normalized_stt = re.sub(r'[^\w\s]', '', sentence.lower())
                normalized_ai = re.sub(r'[^\w\s]', '', self.ai_spoken_buffer.lower())
                # If stt is inside what AI just said, or AI is inside stt, it's an echo.
                if normalized_stt in normalized_ai or normalized_ai in normalized_stt:
                    print(f"[Echo Dropped] Ignoring self-hearing hallucination: '{sentence}'")
                    return

            # --- INTERRUPTION HANDLING & VAD ---
            if self.is_ai_speaking:
                if not is_substantial:
                    # Ignore background noise (e.g. coughs) while AI is speaking
                    return
                else:
                    # It's substantial! 
                    if not getattr(self, "interrupted", False): # Only interrupt and clear ONCE per utterance
                        print(f"[Interrupt] User started speaking: '{sentence}'. Cancelling AI.")
                        self.interrupted = True
                        self._cancel_response_task()
                        await self._clear_twilio_buffer()
                        
                        # Clear any existing debounce buffering so old audio doesn't prepend to the new query
                        self.transcription_buffer = []
                        if self.llm_debounce_task and not getattr(self.llm_debounce_task, 'done', lambda: True)():
                            self.llm_debounce_task.cancel()
                    
                    # If it's an interim result, we've halted the AI. We now wait for the final transcript.
                    if not result.is_final:
                        return

            # If it's just an interim result (and we aren't interrupting), do nothing.
            if not result.is_final:
                return
                
            # Ignore empty finals
            if not sentence:
                return
                
            confidence = result.channel.alternatives[0].confidence or 1.0
            print(f"User (Final chunk): {sentence} (confidence: {confidence:.2f})")
            
            # Fire and forget logging for the chunk
            asyncio.create_task(self._log_event('transcription_chunk', f"{sentence} [conf={confidence:.2f}]"))

            self.interrupted = False  # Reset interrupt flag for the new turn
            self.transcription_buffer.append(sentence)

            # --- DEBOUNCE LOGIC ---
            # Wait 0.4 seconds of silence (Deepgram already waited 500ms) before sending.
            # Total pause time before AI speaks: ~0.9 seconds.
            if getattr(self, "llm_debounce_task", None) and not self.llm_debounce_task.done():
                self.llm_debounce_task.cancel()
            
            async def _process_user_buffer():
                try:
                    await asyncio.sleep(0.4) 
                except asyncio.CancelledError:
                    return # A new speech chunk arrived! Leave the buffer alone and exit.
                    
                full_sentence = " ".join(self.transcription_buffer).strip()
                self.transcription_buffer = [] # Clear buffer for next turn
                
                if not full_sentence:
                    return
                    
                print(f"User (Full Utterance): {full_sentence}")
                asyncio.create_task(self._log_event('transcription', full_sentence))
                asyncio.create_task(self._save_message('user', full_sentence))

                # Check for end-call phrases
                if END_CALL_PATTERN.search(full_sentence):
                    goodbye_msg = "Thank you for calling. Goodbye! Have a great day."
                    asyncio.create_task(self._save_message('assistant', goodbye_msg))
                    asyncio.create_task(self._log_event('call_ended', 'User said goodbye'))
                    
                    async def goodbye_gen(): yield goodbye_msg
                    self._cancel_response_task()
                    self.response_task = asyncio.create_task(self._handle_ai_response(goodbye_gen(), [goodbye_msg]))
                    return

                # Normal flow: Kick off background task for LLM -> TTS stream
                self._cancel_response_task() # Safety clear
                self.response_task = asyncio.create_task(
                    self._generate_and_speak(full_sentence)
                )

            self.llm_debounce_task = asyncio.create_task(_process_user_buffer())

        self.dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)

        options = LiveOptions(
            model="nova-2-phonecall", # better model for telephony
            language="en-US",
            encoding="mulaw",
            channels=1,
            sample_rate=8000,
            interim_results=True, # MUST be True for interruption handling
            endpointing=500, # 500ms of silence to trigger is_final
            smart_format=True,
        )

        if not await self.dg_connection.start(options):
            print("Failed to start Deepgram")
            await self._log_event('error', 'Failed to start Deepgram')

        print("Deepgram started.")

    # ------------------------------------------------------------------
    # Core Pipeline: LLM -> TTS
    # ------------------------------------------------------------------

    async def _generate_and_speak(self, user_text):
        """Main orchestrator for a single conversation turn."""
        self.messages.append({"role": "user", "content": user_text})
        
        # We need a queue to pass words from Groq chunks to ElevenLabs
        # ElevenLabs accepts an AsyncIterator[str].
        
        full_response_parts = []
        
        async def llm_stream_generator():
            try:
                stream = await groq_client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=self.messages,
                    temperature=0.6,
                    max_tokens=150,
                    stream=True
                )
                buffer = ""
                async for chunk in stream:
                    # Defensive check: if task was cancelled or call ended, yield nothing more
                    if self.interrupted or not self.call_active:
                        print("LLM generation interrupted inside generator.")
                        break
                        
                    content = chunk.choices[0].delta.content
                    if content:
                        full_response_parts.append(content)
                        buffer += content
                        self.ai_spoken_buffer += content # Track exactly what is going outward
                        
                        # Semantic Chunking: Yield to ElevenLabs only when a grammatical phrase completes
                        if any(punct in buffer for punct in ['.', '!', '?', ':', '\n']):
                            # Find the latest occurring terminal punctuation
                            last_punct_idx = max(buffer.rfind(p) for p in ['.', '!', '?', ':', '\n'])
                            chunk_to_yield = buffer[:last_punct_idx+1]
                            buffer = buffer[last_punct_idx+1:]
                            yield chunk_to_yield + " " # Yield complete sentence with space padding
                            
                # Flush remaining buffer at the end of the stream
                if buffer.strip():
                    yield buffer + " "
                        
            except asyncio.CancelledError:
                print("LLM stream forcefully cancelled.")
                raise
            except Exception as e:
                print(f"Groq stream error: {e}")
                error_msg = "Sorry, I'm having trouble thinking."
                full_response_parts.append(error_msg)
                yield error_msg

        # Hand off the generator to the speaker task
        try:
            await self._handle_ai_response(llm_stream_generator(), full_response_parts)
        except asyncio.CancelledError:
            print("_generate_and_speak cancelled.")
        finally:
            # Once speaking finishes (or is cancelled), save what we *actually* generated
            final_ai_text = "".join(full_response_parts).strip()
            if final_ai_text:
                self.messages.append({"role": "assistant", "content": final_ai_text})
                print(f"AI: {final_ai_text}")
                asyncio.create_task(self._log_event('ai_response', final_ai_text))
                asyncio.create_task(self._save_message('assistant', final_ai_text))


    async def _handle_ai_response(self, text_iterator, full_text_ref):
        """
        Consumes an async generator of text chunks, feeds it into ElevenLabs TTS,
        and streams the resulting audio back to Twilio.
        """
        if not self.stream_sid or not self.call_active:
            return

        self.is_ai_speaking = True
        self.interrupted = False
        
        # We need the full text for fallback purposes
        full_text = ""

        try:
            # Generate audio incrementally as text arrives — ulaw_8000 is Twilio-compatible
            audio_generator = el_client.text_to_speech.convert_as_stream(
                voice_id=ELEVENLABS_VOICE_ID,
                text=text_iterator, 
                model_id=ELEVENLABS_MODEL,
                output_format="ulaw_8000",
                optimize_streaming_latency=3, # Critical parameter for 10/10 responsiveness
            )

            CHUNK_SIZE = 4000  # Send ~0.5s chunks to minimise latency and buffer build-up
            audio_buffer = b""

            async for chunk in audio_generator:
                if self.interrupted or not self.call_active:
                    print("TTS playback interrupted midway.")
                    break
                    
                audio_buffer += chunk
                
                # Send to Twilio in small pieces as they arrive
                while len(audio_buffer) >= CHUNK_SIZE:
                    send_chunk = audio_buffer[:CHUNK_SIZE]
                    audio_buffer = audio_buffer[CHUNK_SIZE:]
                    
                    b64_audio = base64.b64encode(send_chunk).decode('utf-8')
                    media_payload = {
                        "event": "media",
                        "streamSid": self.stream_sid,
                        "media": {
                            "payload": b64_audio,
                            "track": "outbound"
                        }
                    }
                    if not self.interrupted and self.call_active:
                        await self.send(text_data=json.dumps(media_payload))
            
            # Flush any remaining audio in the buffer
            if audio_buffer and not self.interrupted and self.call_active:
                b64_audio = base64.b64encode(audio_buffer).decode('utf-8')
                media_payload = {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {
                        "payload": b64_audio,
                        "track": "outbound"
                    }
                }
                await self.send(text_data=json.dumps(media_payload))
                
            # Send a mark event so we know when audio has finished playing on the phone
            if not self.interrupted and self.call_active:
                mark_payload = {
                    "event": "mark",
                    "streamSid": self.stream_sid,
                    "mark": {"name": "ai_finished_speaking"}
                }
                await self.send(text_data=json.dumps(mark_payload))

        except asyncio.CancelledError:
            print("_handle_ai_response task cancelled.")
            raise
        except Exception as e:
            print(f"ElevenLabs TTS Error: {e}")
            await self._log_event('error', f"ElevenLabs error. Initiating Twilio Fallback. Error: {e}")
            
            # Fallback to Twilio's standard <Say> voice if ElevenLabs fails
            full_text = "".join(full_text_ref).strip()
            if full_text and self.call_active and self.call_sid:
                try:
                    from twilio.rest import Client
                    client = Client(os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN'])
                    
                    # We can't send TwiML over the media socket, so we modify the live call
                    # We use <Say> and then <Connect><Stream> to rejoin the websocket
                    domain = os.environ.get('DOMAIN')
                    fallback_twiml = f'''
                    <Response>
                        <Say voice="Polly.Joanna-Neural">{full_text}</Say>
                        <Connect>
                            <Stream url="wss://{domain}/ws/media/" />
                        </Connect>
                    </Response>
                    '''
                    await sync_to_async(client.calls)(self.call_sid).update(twiml=fallback_twiml)
                    print("Successfully injected Twilio <Say> fallback.")
                except Exception as fallback_err:
                    print(f"Twilio fallback also failed: {fallback_err}")
            
        finally:
            self.is_ai_speaking = False

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


