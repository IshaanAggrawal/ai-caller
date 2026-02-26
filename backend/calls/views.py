from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from twilio.rest import Client
import os
import json
import certifi

# Fix for PostgreSQL broken SSL cert on Windows breaking python requests
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
os.environ['CURL_CA_BUNDLE'] = certifi.where()

@csrf_exempt
def make_call(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            to_phone_number = data.get('to')
            if not to_phone_number:
                return JsonResponse({'error': 'Missing "to" phone number'}, status=400)

            account_sid = os.environ['TWILIO_ACCOUNT_SID']
            auth_token = os.environ['TWILIO_AUTH_TOKEN']
            from_phone_number = os.environ['TWILIO_PHONE_NUMBER']
            domain = os.environ.get('DOMAIN') # ngrok domain for django
            
            if not domain:
                 return JsonResponse({'error': 'Missing DOMAIN in .env'}, status=500)

            client = Client(account_sid, auth_token)

            call = client.calls.create(
                url=f"https://{domain}/calls/twiml/",
                to=to_phone_number,
                from_=from_phone_number
            )

            return JsonResponse({'message': 'Call initiated', 'call_sid': call.sid})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'Only POST method allowed'}, status=405)

@csrf_exempt
def twiml(request):
    """
    Twilio requests this endpoint when the user answers.
    We return TwiML to connect the call to our FastAPI WebSocket.
    """
    if request.method == 'POST':
        domain = os.environ.get('DOMAIN')
        # We assume FastAPI will run on the same ngrok domain but we might need
        # a separate ngrok tunnel for fastapi. For now, let's assume we use the same domain 
        # or a specific websocket domain. Let's add FASTAPI_DOMAIN to env to be safe.
        fastapi_domain = os.environ.get('FASTAPI_DOMAIN', domain)

        if not fastapi_domain:
            return HttpResponse("Missing FASTAPI_DOMAIN in environment variables.", status=500)

        response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{fastapi_domain}/media-stream" />
    </Connect>
</Response>"""
        return HttpResponse(response, content_type='text/xml')
    return HttpResponse('Only POST method allowed', status=405)
