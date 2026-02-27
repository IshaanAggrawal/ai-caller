from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'media-stream$', consumers.TwilioMediaConsumer.as_asgi()),
]
