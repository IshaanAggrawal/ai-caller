import subprocess
import time
import json
import urllib.request
import re
import os


def start_and_configure_ngrok():
    """Start a single ngrok tunnel for the unified Django+Channels server on port 8000."""

    print("Starting ngrok on port 8000...")
    ngrok_proc = subprocess.Popen(
        ["ngrok.exe", "http", "8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for the tunnel to establish
    time.sleep(3)

    # Get the public URL from the local ngrok API
    public_url = None
    try:
        req = urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels")
        data = json.loads(req.read())
        for tunnel in data['tunnels']:
            if tunnel['config']['addr'] == 'http://localhost:8000':
                public_url = tunnel['public_url']
                break
        if not public_url and data['tunnels']:
            public_url = data['tunnels'][0]['public_url']
    except Exception as e:
        print(f"Error fetching ngrok URL: {e}")
        return

    if not public_url:
        print("Could not find ngrok tunnel URL.")
        return

    domain = public_url.replace("https://", "").replace("http://", "")
    print(f"Public URL: {public_url}")
    print(f"Domain: {domain}")

    # Update .env
    with open(".env", "r") as f:
        content = f.read()

    content = re.sub(r"DOMAIN=.*", f"DOMAIN={domain}", content)

    with open(".env", "w") as f:
        f.write(content)

    print(".env updated successfully.")
    print(f"\nServer ready! Run: daphne -b 0.0.0.0 -p 8000 core.asgi:application")


if __name__ == "__main__":
    start_and_configure_ngrok()
