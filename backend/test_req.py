import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request(
    'http://localhost:8000/calls/make-call/', 
    data=json.dumps({'to': '+919258895224'}).encode('utf-8'), 
    headers={'Content-Type': 'application/json'}
)

try:
    response = urllib.request.urlopen(req, context=ctx)
    print(response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(e.read().decode('utf-8'))
except Exception as e:
    print(e)
