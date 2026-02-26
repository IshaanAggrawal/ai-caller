import subprocess
import time
import json
import urllib.request
import re
import os

def start_and_configure_ngrok():
    # Start ngrok for Django (port 8000)
    print("Starting ngrok on port 8000...")
    django_ngrok = subprocess.Popen(["ngrok.exe", "http", "8000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Wait a bit for the tunnel to establish
    time.sleep(3)
    
    # Get the public URL from the local ngrok API (port 4040)
    django_url = None
    try:
        req = urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels")
        data = json.loads(req.read())
        for tunnel in data['tunnels']:
            if tunnel['config']['addr'] == 'http://localhost:8000':
                django_url = tunnel['public_url']
                break
    except Exception as e:
        print(f"Error fetching Django ngrok url: {e}")

    # Start ngrok for FastAPI (port 8001)
    # ngrok on the free tier only allows 1 process to run at a time unless you use a config file.
    # We will try to start a second one, but it might fail.
    print("Starting ngrok on port 8001...")
    fastapi_ngrok = subprocess.Popen(["ngrok.exe", "http", "8001"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    time.sleep(3)
    fastapi_url = None
    try:
        # The second process might start its API on port 4041
        req = urllib.request.urlopen("http://127.0.0.1:4041/api/tunnels")
        data = json.loads(req.read())
        fastapi_url = data['tunnels'][0]['public_url']
    except Exception as e:
         print(f"Error fetching FastAPI ngrok url from 4041: {e}")
         # If 4041 failed, maybe it's on 4040 if the config allowed multiple tunnels
         try:
            req = urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels")
            data = json.loads(req.read())
            for tunnel in data['tunnels']:
                if tunnel['config']['addr'] == 'http://localhost:8001':
                    fastapi_url = tunnel['public_url']
                    break
         except Exception as e2:
             pass
                

    print(f"Django URL: {django_url}")
    print(f"FastAPI URL: {fastapi_url}")

    if django_url or fastapi_url:
        print("Updating .env file...")
        with open(".env", "r") as f:
            content = f.read()

        if django_url:
            domain_only = django_url.replace("https://", "").replace("http://", "")
            content = re.sub(r"DOMAIN=.*", f"DOMAIN={domain_only}", content)
            
        if fastapi_url:
            fapi_domain_only = fastapi_url.replace("https://", "").replace("http://", "")
            content = re.sub(r"FASTAPI_DOMAIN=.*", f"FASTAPI_DOMAIN={fapi_domain_only}", content)

        with open(".env", "w") as f:
            f.write(content)
        print(".env updated successfully")
    
    # We will leave the processes running in the background for the user.

if __name__ == "__main__":
    start_and_configure_ngrok()
