from rest_framework.test import APITestCase
from rest_framework import status
from django.urls import reverse
from calls.models import CallSession, CallEvent
import json
from unittest.mock import patch, MagicMock

class CallEndpointsTests(APITestCase):

    def setUp(self):
        # Create some initial test data
        self.session = CallSession.objects.create(
            call_sid="CAautotest123",
            from_number="+919999999999",
            to_number="+16812816509",
            status="initiated"
        )
        CallEvent.objects.create(
            session=self.session,
            event_type="call_initiated",
            detail="Test setup call initiated"
        )

    def test_1_health_check(self):
        """GET /calls/health/ returns ok"""
        response = self.client.get(reverse('health'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ok')
        self.assertEqual(response.data['service'], 'ai-voice-caller')

    def test_2_call_history(self):
        """GET /calls/call-history/ returns list"""
        response = self.client.get(reverse('call_history'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('total', response.data)
        self.assertIn('results', response.data)
        self.assertIsInstance(response.data['results'], list)
        self.assertGreaterEqual(response.data['total'], 1)

    def test_3_call_detail_404(self):
        """GET /calls/call-detail/<unknown> returns 404"""
        response = self.client.get(reverse('call_detail', args=['CA_DOES_NOT_EXIST']))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_4_inbound_webhook(self):
        """POST /calls/inbound/ returns valid TwiML"""
        data = {
            "CallSid": "CA_NEW_INBOUND",
            "From": "+1234567890",
            "To": "+0987654321"
        }
        # Inbound payload is www-form-urlencoded
        response = self.client.post(reverse('inbound'), data, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'text/xml')
        
        # Verify body contains <Connect> and <Stream> for wss
        content = response.content.decode()
        self.assertIn('<Stream', content)
        self.assertIn('wss://', content)
        self.assertIn('/media-stream', content)

    def test_5_call_status_webhook(self):
        """POST /calls/call-status/ processes status update"""
        data = {
            "CallSid": "CAautotest123",
            "CallStatus": "in-progress",
            "CallDuration": "0"
        }
        response = self.client.post(reverse('call_status'), data, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'received')

        # Verify DB updated
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "in_progress")

    def test_6_saved_inbound_call(self):
        """GET /calls/call-detail/ shows saved inbound call"""
        response = self.client.get(reverse('call_detail', args=['CAautotest123']))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['call_sid'], 'CAautotest123')
        self.assertEqual(response.data['from_number'], '+919999999999')

    @patch('groq.Groq')
    def test_7_test_chat_llm(self, mock_groq_class):
        """POST /calls/test-chat/ gets LLM response (MOCKED)"""
        # Mock the Groq client
        mock_client = MagicMock()
        mock_groq_class.return_value = mock_client
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "This is a mock AI response."
        mock_client.chat.completions.create.return_value = mock_completion
        
        data = {
            "message": "Hello, what is 2+2?",
            "session_id": "autotest",
            "system_prompt": "You are a test bot."
        }
        response = self.client.post(reverse('test_chat'), data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['response'], "This is a mock AI response.")
        self.assertEqual(response.data['turn'], 1)
        mock_client.chat.completions.create.assert_called_once()

    def test_8_test_page_html(self):
        """GET /calls/test/ returns working HTML page"""
        response = self.client.get(reverse('test_page'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        content = response.content.decode()
        self.assertIn('AI Voice Caller', content)
        self.assertIn('test-voice', content)

    @patch('calls.views.Client')
    def test_9_make_call_outbound(self, mock_twilio_client_class):
        """POST /calls/make-call/ simulates outbound call (MOCKED)"""
        # Mock Twilio Client
        mock_client = MagicMock()
        mock_twilio_client_class.return_value = mock_client
        mock_call = MagicMock()
        mock_call.sid = "CA_MOCK_OUTBOUND_123"
        mock_client.calls.create.return_value = mock_call
        
        data = {
            "to": "+19998887777",
            "system_prompt": "Test prompt"
        }
        
        with patch.dict('os.environ', {
            'TWILIO_ACCOUNT_SID': 'AC_fake',
            'TWILIO_AUTH_TOKEN': 'fake_token',
            'TWILIO_PHONE_NUMBER': '+10000000000',
            'DOMAIN': 'test.com'
        }):
            response = self.client.post(reverse('make_call'), data, format='json')
            
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['call_sid'], "CA_MOCK_OUTBOUND_123")
        self.assertEqual(response.data['message'], "Call initiated")
        
        # Verify Twilio client was called with right args
        mock_client.calls.create.assert_called_once()
        call_kwargs = mock_client.calls.create.call_args[1]
        self.assertEqual(call_kwargs['to'], "+19998887777")
        self.assertEqual(call_kwargs['url'], "https://test.com/calls/twiml/")

    @patch('groq.Groq')
    @patch('elevenlabs.ElevenLabs')
    def test_10_test_voice_llm_and_tts(self, mock_elevenlabs_class, mock_groq_class):
        """POST /calls/test-voice/ gets LLM and TTS response (MOCKED)"""
        # Mock Groq
        mock_groq_client = MagicMock()
        mock_groq_class.return_value = mock_groq_client
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "Mock voice response."
        mock_groq_client.chat.completions.create.return_value = mock_completion
        
        # Mock ElevenLabs
        mock_el_client = MagicMock()
        mock_elevenlabs_class.return_value = mock_el_client
        # simulate returning an iterable of bytes
        mock_el_client.text_to_speech.convert.return_value = [b"mock", b"audio", b"data"]
        
        data = {
            "message": "Speak to me",
            "session_id": "voice_test"
        }
        
        response = self.client.post(reverse('test_voice'), data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['response'], "Mock voice response.")
        self.assertEqual(response.data['audio_format'], "mp3")
        # Base64 of "mockaudiodata" is bW9ja2F1ZGlvZGF0YQ==
        self.assertEqual(response.data['audio'], "bW9ja2F1ZGlvZGF0YQ==")
        self.assertEqual(response.data['session_id'], "voice_test")
        
        mock_groq_client.chat.completions.create.assert_called_once()
        mock_el_client.text_to_speech.convert.assert_called_once()
