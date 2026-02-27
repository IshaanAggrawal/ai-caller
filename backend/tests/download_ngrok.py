import urllib.request
import zipfile
import os
import platform

def download_ngrok():
    print("Detected OS:", platform.system())
    url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
    zip_path = "ngrok.zip"
    
    print(f"Downloading ngrok from {url}...")
    urllib.request.urlretrieve(url, zip_path)
    
    print("Extracting ngrok...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(".")
        
    print("Cleaning up zip file...")
    os.remove(zip_path)
    
    print("Done! ngrok.exe is now in the current folder.")

if __name__ == "__main__":
    download_ngrok()
