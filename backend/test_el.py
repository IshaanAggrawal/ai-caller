import asyncio
import os
from elevenlabs.client import AsyncElevenLabs

async def run():
    client = AsyncElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY", ""))
    
    # Check if the convert method is async
    print("starting convert...")
    gen = await client.text_to_speech.convert(
        voice_id="21m00Tcm4TlvDq8ikWAM",
        text="Hello world",
        model_id="eleven_turbo_v2",
        output_format="ulaw_8000"
    )
    
    chunks = []
    async for chunk in gen:
        chunks.append(chunk)
        
    print(f"Got {len(chunks)} chunks")

if __name__ == "__main__":
    asyncio.run(run())
