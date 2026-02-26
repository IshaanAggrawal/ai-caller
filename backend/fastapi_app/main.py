import os
import json
import base64
import asyncio
from fastapi import FastAPI, WebSocket
from dotenv import load_dotenv

# Provide a specific environment path depending on where uvicorn is started
load_dotenv()

from deepgram import (
    DeepgramClient,
    LiveTranscriptionEvents,
    LiveOptions,
)
from groq import AsyncGroq
from cartesia import AsyncCartesia

app = FastAPI()

# SDK Clients
deepgram = DeepgramClient(os.environ.get("DEEPGRAM_API_KEY", ""))
groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY", ""))
cartesia_client = AsyncCartesia(api_key=os.environ.get("CARTESIA_API_KEY", ""))

# Cartesia defaults
CARTESIA_VOICE_ID = "a0e99841-438c-4a64-b6a9-62f2d8d80f62" # Example public voice
CARTESIA_MODEL = "sonic-english"

# Context messages string for simplicity
SYSTEM_PROMPT = "You are a helpful, brief, and friendly AI phone assistant. Always speak conversationally."


@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket connection accepted.")
    
    stream_sid = None
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    # State flags
    call_active = True
    
    # Initialize Deepgram Live Connection
    dg_connection = deepgram.listen.asyncwebsocket.v("1")
    
    async def get_groq_response(user_text):
        messages.append({"role": "user", "content": user_text})
        try:
            completion = await groq_client.chat.completions.create(
                model="llama3-8b-8192",
                messages=messages,
                temperature=0.6,
                max_tokens=150,
            )
            response_text = completion.choices[0].message.content
            messages.append({"role": "assistant", "content": response_text})
            return response_text
        except Exception as e:
            print(f"Groq Error: {e}")
            return "I'm sorry, I'm having trouble thinking right now."

    async def speak_text(text):
        if not stream_sid: return
        try:
            # Generate audio with Cartesia
            # For twilio, we need sample_rate=8000, encoding="mulaw"
            ws = await cartesia_client.tts.websocket()
            output_format = {
                "container": "raw",
                "encoding": "mulaw",
                "sample_rate": 8000
            }
            context_id = await ws.generate(
                transcript=text,
                voice_id=CARTESIA_VOICE_ID,
                model_id=CARTESIA_MODEL,
                output_format=output_format
            )
            
            async for message in ws.receive():
                if "audio" in message:
                    audio_bytes = message["audio"]
                    b64_audio = base64.b64encode(audio_bytes).decode('utf-8')
                    media_payload = {
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {
                            "payload": b64_audio
                        }
                    }
                    await websocket.send_text(json.dumps(media_payload))
                if message.get("done"):
                    break
            
            await ws.close()
        except Exception as e:
             print(f"Cartesia Error: {e}")

    async def on_message(self, result, **kwargs):
        sentence = result.channel.alternatives[0].transcript
        if len(sentence) == 0:
            return
        if result.is_final:
            print(f"User: {sentence}")
            ai_response = await get_groq_response(sentence)
            print(f"AI: {ai_response}")
            await speak_text(ai_response)
            
    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)

    try:
        options = LiveOptions(
            model="nova-2",
            language="en-US",
            encoding="mulaw",
            channels=1,
            sample_rate=8000,
            interim_results=False, # Wait for full sentence to avoid spamming Groq
        )
        if not await dg_connection.start(options):
            print("Failed to start Deepgram")
            return
            
        print("Deepgram started.")
        
        while call_active:
            message = await websocket.receive_text()
            data = json.loads(message)
            
            if data['event'] == 'start':
                stream_sid = data['start']['streamSid']
                print(f"Call started. Stream SID: {stream_sid}")
                # Optional: The agent can say hello first here
                await speak_text("Hello! How can I help you today?")
                
            elif data['event'] == 'media':
                # Twilio sends base64 mulaw audio
                audio_b64 = data['media']['payload']
                audio_bytes = base64.b64decode(audio_b64)
                await dg_connection.send(audio_bytes)
                
            elif data['event'] == 'stop':
                print("Call stopped by Twilio.")
                call_active = False
                
    except Exception as e:
        print(f"WebSocket Error: {e}")
    finally:
        await dg_connection.finish()
        if not websocket.client_state.name == "DISCONNECTED":
            await websocket.close()
