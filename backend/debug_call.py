import os
from twilio.rest import Client
from dotenv import load_dotenv
import certifi

os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
os.environ['CURL_CA_BUNDLE'] = certifi.where()

load_dotenv()

account_sid = os.environ['TWILIO_ACCOUNT_SID']
auth_token = os.environ['TWILIO_AUTH_TOKEN']
client = Client(account_sid, auth_token)

call_sid = 'CA8d95a2cbaf4a33938069bd537f9f5207'

try:
    notifications = client.calls(call_sid).notifications.list()
    for n in notifications:
         print(f"Error Code: {n.error_code} - {n.message_text}")
except Exception as e:
    print(f"Failed to fetch: {e}")
