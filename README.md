# AI Voice Caller 🎙️

> Production-grade AI voice agent — makes and receives phone calls with real-time speech recognition, intelligent conversation, and natural human voice.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Django](https://img.shields.io/badge/Django-6.0-green.svg)](https://djangoproject.com)
[![License: ISC](https://img.shields.io/badge/License-ISC-yellow.svg)](https://opensource.org/licenses/ISC)

**Built with:** Python · Django Channels · Daphne (ASGI) · Twilio · Deepgram · Groq · ElevenLabs

---

## Table of Contents

- [Architecture](#architecture)
- [How Calls Work](#how-calls-work)
- [Features](#features)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [Database Configuration](#database-configuration)
- [API Reference](#api-reference)
- [Testing](#testing)
- [Deployment](#deployment)
- [Security & Compliance](#security--compliance)
- [Responsible AI Use](#responsible-ai-use)
- [Latency & Performance](#latency--performance)
- [Cost Breakdown](#cost-breakdown)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                   Daphne ASGI Server (Port 8000)             │
│               HTTP + WebSocket on a single process           │
│                                                              │
│  ┌─────────────────┐      ┌────────────────────────────┐     │
│  │  Django HTTP     │      │  Django Channels WebSocket │     │
│  │                  │      │  /media-stream             │     │
│  │  POST /make-call │      │                            │     │
│  │  POST /inbound   │      │  Twilio mulaw audio (8kHz) │     │
│  │  GET  /health    │      │        ↓                   │     │
│  │  GET  /test      │      │  Deepgram STT (Nova-2)     │     │
│  │  GET  /voice-test│      │        ↓                   │     │
│  │  GET  /history   │      │  Groq LLM (Llama 3.1)     │     │
│  │                  │      │        ↓                   │     │
│  │                  │      │  ElevenLabs TTS (ulaw)     │     │
│  │                  │      │        ↓                   │     │
│  │                  │      │  Audio → Twilio → Caller   │     │
│  └────────┬─────────┘      └──────────┬─────────────────┘     │
│           │                           │                       │
│  ┌────────┴───────────────────────────┴──────┐                │
│  │       SQLite (dev) / PostgreSQL (prod)    │                │
│  │  CallSession · ConversationMessage · Event│                │
│  └───────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────┘
         │                          │
    ┌────┴─────┐            ┌───────┴───────┐
    │  Twilio  │            │  ngrok/Render │
    │  (PSTN)  │            │  (tunnel/host)│
    └──────────┘            └───────────────┘
```

**Single-process architecture** — HTTP and WebSocket on one port. No separate servers, no message brokers, no Redis.

---

## How Calls Work

### Outbound Call Flow

```
POST /calls/make-call/ {"to": "+91..."}
  │
  ├─→ Creates CallSession in database
  ├─→ Calls Twilio REST API to dial number
  │
  │   [Person answers]
  │
  ├─→ Twilio requests GET /calls/twiml/
  ├─→ Returns: <Connect><Stream wss://domain/media-stream/>
  │
  │   [WebSocket opens — real-time voice loop]
  │
  │   Person speaks
  │     → Twilio streams mulaw audio chunks
  │     → Deepgram transcribes (real-time, ~300ms)
  │     → Groq generates AI response (~500ms)
  │     → ElevenLabs converts to speech (~800ms)
  │     → Audio chunks sent back to Twilio
  │     → Person hears AI voice
  │
  │   [Repeats until "goodbye"/"bye"/"hang up" detected]
  │
  └─→ Twilio sends status to /calls/call-status/
      Session marked complete with duration
```

### Inbound Call Flow

```
Person dials your Twilio number
  → Twilio hits POST /calls/inbound/
  → Creates CallSession in DB
  → Returns TwiML with WebSocket stream
  → Same real-time voice loop as outbound
```

### With External Context (CRM Integration)

```json
POST /calls/make-call/
{
    "to": "+919876543210",
    "system_prompt": "You are a loan recovery agent for ABC Bank.",
    "context_url": "https://your-crm.com/api/customer/123",
    "context_headers": {"Authorization": "Bearer your_token"}
}
```

Before the AI speaks, it fetches the context URL and injects the response into its system prompt. The AI then talks **with full context** about the caller.

---

## Features

| Category | Feature | Description |
|----------|---------|-------------|
| **Calling** | Outbound calls | Dial any number programmatically via API |
| | Inbound calls | Handle calls to your Twilio number |
| | Call status tracking | Real-time status updates (ringing → answered → completed) |
| **AI Pipeline** | Real-time STT | Deepgram Nova-2 via WebSocket streaming |
| | LLM responses | Groq Llama 3.1 8B Instant (free, fast) |
| | Natural voice | ElevenLabs TTS (human-quality) with browser TTS fallback |
| | Context API | Fetch caller info from CRM/database before call |
| | Conversation memory | Full conversation context maintained |
| **Smart Features** | Goodbye detection | Auto-detects "bye", "goodbye", "hang up" (English + Hindi) |
| | Low confidence reprompt | "I didn't catch that" when audio is unclear |
| | TTS fallback | Graceful degradation if ElevenLabs fails |
| **Logging** | Database logging | Every call, message, and event stored |
| | Django Admin | Browse transcripts and events at `/admin/` |
| | Call history API | Paginated call logs and full transcripts |
| **Testing** | Browser voice call | Speak into mic, hear AI respond (no Twilio needed) |
| | Text chat test | Type and hear responses |
| | Automated test suite | 10 endpoint tests in one command |
| | Health check | Service monitoring endpoint |

---

## Quick Start

### Prerequisites

- Python 3.10+
- API keys for: Twilio, Groq, Deepgram, ElevenLabs
- ngrok (for local development with Twilio)

### 1. Clone & Install

```bash
git clone https://github.com/your-username/ai-caller.git
cd ai-caller/backend

python -m venv venv
.\venv\Scripts\activate        # Windows
# source venv/bin/activate     # Linux/Mac

pip install -r requirements.txt
```

### 2. Configure Environment

Create `backend/.env`:

```env
# Twilio — https://console.twilio.com
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx

# Domain — auto-updated by auto_ngrok.py (or set to Render URL)
DOMAIN=your-subdomain.ngrok-free.dev

# Groq LLM — https://console.groq.com (FREE)
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Deepgram STT — https://console.deepgram.com ($200 free credit)
DEEPGRAM_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ElevenLabs TTS — https://elevenlabs.io (10,000 chars/month free)
ELEVENLABS_API_KEY=sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Optional
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM     # "Rachel" (default)
ELEVENLABS_MODEL=eleven_turbo_v2               # Fastest model
```

### 3. Database Setup

```bash
python manage.py migrate
python manage.py createsuperuser    # For Django Admin
```

### 4. Run

```bash
# Terminal 1: Start ngrok (for Twilio webhooks)
python auto_ngrok.py

# Terminal 2: Start server
daphne -b 0.0.0.0 -p 8000 core.asgi:application
```

### 5. Configure Twilio

In [Twilio Console → Phone Numbers → Your Number](https://console.twilio.com):
- **Voice Webhook:** `https://<your-ngrok>/calls/inbound/` (POST)

### 6. Test

```bash
# Open in Chrome — speak into mic, hear AI respond
start chrome http://localhost:8000/calls/voice-test/
```

---

## Environment Variables

| Variable | Required | Source | Free Tier |
|----------|----------|--------|-----------|
| `TWILIO_ACCOUNT_SID` | ✅ | [Twilio Console](https://console.twilio.com) | ~$15 trial credit |
| `TWILIO_AUTH_TOKEN` | ✅ | [Twilio Console](https://console.twilio.com) | — |
| `TWILIO_PHONE_NUMBER` | ✅ | [Twilio Phone Numbers](https://console.twilio.com/us1/develop/phone-numbers) | 1 free trial number |
| `DOMAIN` | ✅ | `auto_ngrok.py` or Render URL | — |
| `GROQ_API_KEY` | ✅ | [Groq Console](https://console.groq.com) | ✅ Free |
| `DEEPGRAM_API_KEY` | ✅ | [Deepgram Console](https://console.deepgram.com) | $200 free credit |
| `ELEVENLABS_API_KEY` | ✅ | [ElevenLabs](https://elevenlabs.io) | 10K chars/month |
| `ELEVENLABS_VOICE_ID` | ❌ | [Voice Library](https://elevenlabs.io/voice-library) | Default: Rachel |
| `ELEVENLABS_MODEL` | ❌ | [ElevenLabs Docs](https://elevenlabs.io/docs) | Default: eleven_turbo_v2 |
| `SECRET_KEY` | ❌ | Generate for prod | Auto-generated |
| `DEBUG` | ❌ | Set `False` in prod | Default: True |

---

## Database Configuration

### Development: SQLite (Default)

Zero configuration — database file is `backend/db.sqlite3`.

### Production: PostgreSQL

```bash
pip install psycopg2-binary
```

Update `backend/core/settings.py`:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'ai_caller',
        'USER': 'your_user',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

Or use `DATABASE_URL` env var (recommended for Render/Supabase):

```env
DATABASE_URL=postgresql://user:password@host:5432/ai_caller
```

### Database Schema

| Table | Fields | Purpose |
|-------|--------|---------|
| `CallSession` | call_sid, from/to number, status, duration, system_prompt, context_url/data | Call lifecycle |
| `ConversationMessage` | session FK, role (user/assistant), content, timestamp | Full transcript |
| `CallEvent` | session FK, event_type, detail, timestamp | Debug events |

---

## API Reference

### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/calls/health/` | Health check |
| `POST` | `/calls/make-call/` | Initiate outbound call |
| `POST` | `/calls/inbound/` | Twilio inbound webhook |
| `POST` | `/calls/twiml/` | TwiML for outbound calls |
| `POST` | `/calls/call-status/` | Twilio status webhook |
| `GET` | `/calls/call-history/` | Paginated call logs |
| `GET` | `/calls/call-detail/<call_sid>/` | Full transcript & events |

### Test Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/calls/voice-test/` | 🎙️ Browser voice call (mic → AI → speaker) |
| `GET` | `/calls/test/` | 💬 Browser text chat |
| `POST` | `/calls/test-chat/` | API: text → AI text |
| `POST` | `/calls/test-voice/` | API: text → AI text + audio |

### Example Requests

```bash
# Basic outbound call
curl -X POST http://localhost:8000/calls/make-call/ \
  -H "Content-Type: application/json" \
  -d '{"to": "+919876543210"}'

# With context API
curl -X POST http://localhost:8000/calls/make-call/ \
  -H "Content-Type: application/json" \
  -d '{
    "to": "+919876543210",
    "system_prompt": "You are a loan recovery agent.",
    "context_url": "https://api.example.com/customer/123",
    "context_headers": {"Authorization": "Bearer token"}
  }'

# Chat test
curl -X POST http://localhost:8000/calls/test-chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, who are you?"}'

# Call history
curl http://localhost:8000/calls/call-history/?page=1&per_page=10
```

---

## Testing

### Method 1: Browser Voice Call (Recommended First Test)

**No Twilio needed.** Tests LLM + TTS pipeline.

```
Open: http://localhost:8000/calls/voice-test/
```

1. Click green 📞 button → Allow microphone
2. Speak → AI responds with voice
3. Continuous conversation loop (like a real call)
4. Click red button to end

> Requires Chrome. If ElevenLabs fails, falls back to browser built-in voice.

### Method 2: Browser Text Chat

```
Open: http://localhost:8000/calls/test/
```

Type messages, hear AI respond. Toggle 🔊 Voice for audio.

### Method 3: Automated Test Suite

```bash
cd backend
set PYTHONIOENCODING=utf-8
.\venv\Scripts\python.exe tests\test_all.py
```

Runs 10 tests: health check, call history, inbound webhook, call status, DB verification, LLM chat, conversation memory, voice pipeline, test page.

### Method 4: curl / Postman

```bash
# Health check
curl http://localhost:8000/calls/health/

# Chat test
curl -X POST http://localhost:8000/calls/test-chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello"}'
```

Postman collection included: `backend/tests/ai_caller.postman_collection.json`

### Method 5: Full End-to-End Call

```bash
# Terminal 1
python auto_ngrok.py

# Terminal 2
daphne -b 0.0.0.0 -p 8000 core.asgi:application

# Terminal 3
curl -X POST http://localhost:8000/calls/make-call/ \
  -H "Content-Type: application/json" \
  -d '{"to": "+91YOUR_NUMBER"}'
```

> **Twilio Geo-Permissions:** Enable India (+91) at  
> [Twilio Console → Voice → Geo Permissions](https://console.twilio.com/us1/develop/voice/settings/geo-permissions)

---

## Deployment

### Render (Recommended)

**Files included:** `Procfile`, `build.sh`, `render.yaml`

1. Push code to GitHub
2. Go to [render.com/new](https://render.com/new) → **New Web Service**
3. Connect your repo
4. Settings:
   - **Root Directory:** `backend`
   - **Build Command:** `./build.sh`
   - **Start Command:** `daphne -b 0.0.0.0 -p $PORT core.asgi:application`
5. Add environment variables (see [Environment Variables](#environment-variables))
6. Set `DOMAIN` to your Render URL (e.g., `ai-voice-caller.onrender.com`)
7. Deploy!

**After deploy:**
- Update Twilio webhook to `https://your-app.onrender.com/calls/inbound/`
- Test: `https://your-app.onrender.com/calls/health/`

### VPS / Linux Server

```bash
# 1. Install
sudo apt update && sudo apt install python3.11 python3.11-venv postgresql nginx
git clone <repo> /opt/ai-caller && cd /opt/ai-caller/backend
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt psycopg2-binary

# 2. Database
sudo -u postgres createdb ai_caller
# Update DATABASE_URL in .env

# 3. Migrations
python manage.py migrate && python manage.py createsuperuser

# 4. systemd service
sudo tee /etc/systemd/system/ai-caller.service << 'EOF'
[Unit]
Description=AI Voice Caller
After=network.target postgresql.service
[Service]
User=www-data
WorkingDirectory=/opt/ai-caller/backend
ExecStart=/opt/ai-caller/backend/venv/bin/daphne -b 0.0.0.0 -p 8000 core.asgi:application
Restart=always
EnvironmentFile=/opt/ai-caller/backend/.env
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable ai-caller && sudo systemctl start ai-caller

# 5. nginx (with WebSocket support)
sudo tee /etc/nginx/sites-available/ai-caller << 'EOF'
server {
    listen 443 ssl;
    server_name your-domain.com;
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    location /media-stream {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/ai-caller /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl restart nginx
```

---

## Security & Compliance

### ⚠️ Regulatory Compliance

AI voice calling is regulated in most countries. **You are responsible for complying with all applicable laws.**

| Regulation | Region | Key Requirements |
|------------|--------|-----------------|
| **TCPA** | United States | Prior express consent required for automated calls. Robocall restrictions. Do-Not-Call registry compliance. |
| **TRAI DND** | India | Check Do-Not-Disturb (DND) registry. TRAI regulations on telemarketing. Commercial calls only 9 AM - 9 PM IST. |
| **GDPR** | European Union | Explicit consent for call recording. Right to erasure of call data. Data processing agreements required. |
| **PECR** | United Kingdom | Consent for automated calls. ICO regulations on marketing calls. |
| **CASL** | Canada | Express or implied consent for commercial calls. |

### Technical Security Measures

| Area | Implementation |
|------|---------------|
| **API Keys** | Stored in `.env` file, never committed to git |
| **CSRF Protection** | Django CSRF middleware enabled, trusted origins configured |
| **Secret Key** | Loaded from environment variable in production |
| **Debug Mode** | Disabled in production via `DEBUG=False` |
| **Allowed Hosts** | Configured for specific domains |
| **HTTPS** | Required for Twilio webhooks (ngrok/Render provide this) |
| **Database** | PostgreSQL with authentication in production |

### Recommended Security Hardening

```python
# Add to settings.py for production
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
```

---

## Responsible AI Use

### ⚡ Ethical Guidelines

This project provides the **technology** for AI voice calling. How it is used is **your responsibility**.

#### ✅ Acceptable Use Cases

- Customer support and helpdesk automation
- Appointment reminders and scheduling
- Survey and feedback collection (with consent)
- Internal business communication
- Emergency notifications
- Educational and research purposes

#### ❌ Prohibited Use Cases

- Spam calls or unsolicited telemarketing
- Impersonation or social engineering
- Fraud, scams, or deceptive practices
- Harassment or intimidation
- Political robocalls without compliance
- Any use that violates applicable law

### Best Practices

1. **Always disclose** that the caller is an AI at the start of every call
2. **Obtain consent** before making automated calls
3. **Provide opt-out** — allow recipients to request no further calls
4. **Respect DND registers** — check Do-Not-Disturb lists before calling
5. **Limit call hours** — only call during reasonable hours (9 AM - 9 PM local time)
6. **Log everything** — maintain call records for compliance auditing
7. **Secure data** — encrypt call transcripts and protect personal data
8. **Regular audits** — review AI responses for accuracy and bias
9. **Human escalation** — provide option to transfer to a human agent
10. **Data retention** — implement data deletion policies per GDPR/local laws

### AI Disclosure Template

Add this to your system prompt for compliance:

```
"At the start of every call, say: 'Hello, this is an AI assistant calling 
on behalf of [Company Name]. This call may be recorded for quality purposes. 
You can ask to speak with a human agent at any time, or say stop to end this call.'"
```

---

## Latency & Performance

| Stage | Latency |
|-------|---------|
| Network (Twilio ↔ Server) | ~100ms |
| Deepgram STT | ~300-500ms |
| Groq LLM (Llama 3.1 8B Instant) | ~500-800ms |
| ElevenLabs TTS | ~800-1200ms |
| Network (Server ↔ Twilio) | ~100ms |
| **Total round-trip** | **~1.8 - 2.6s** |

This is within industry standard. Production voice agents (Bland.ai, Retell, SquadStack) typically have 1.5-3s latency.

### Known Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| No barge-in | AI doesn't stop when interrupted | Add Twilio `clear` message support |
| Blocking TTS | Full audio generated before streaming | Use ElevenLabs WebSocket API |
| SQLite concurrency | Single-writer for concurrent calls | Use PostgreSQL in production |
| No retry logic | API failures not retried | Add exponential backoff |
| No rate limiting | API abuse possible | Add Django throttling |

---

## Cost Breakdown

| Service | Free Tier | Paid Pricing |
|---------|-----------|-------------|
| **Groq** (LLM) | ✅ Free (rate limited) | Pay-per-token |
| **Deepgram** (STT) | $200 credit on signup | $0.0043/min |
| **ElevenLabs** (TTS) | 10,000 chars/month | From $5/month |
| **Twilio** (Calls) | ~$15 trial credit | ~$0.013/min outbound |
| **ngrok** (Tunnel) | 1 free tunnel | From $8/month |
| **Render** (Hosting) | Free tier available | From $7/month |

**Estimated cost per 1-minute call:** ~$0.02 (after free tiers)

---

## Project Structure

```
ai-caller/
├── README.md
├── render.yaml                           # Render deployment blueprint
├── .gitignore
└── backend/
    ├── manage.py
    ├── requirements.txt
    ├── Procfile                          # Render process file
    ├── build.sh                          # Render build script
    ├── auto_ngrok.py                     # ngrok auto-tunnel
    ├── .env                              # Environment variables (not committed)
    ├── core/
    │   ├── settings.py                   # Django config
    │   ├── asgi.py                       # ASGI routing (HTTP + WebSocket)
    │   ├── urls.py                       # Root URLs
    │   └── wsgi.py
    ├── calls/
    │   ├── models.py                     # DB models
    │   ├── consumers.py                  # WebSocket (STT → LLM → TTS)
    │   ├── views.py                      # HTTP endpoints + test UIs
    │   ├── urls.py                       # URL routing
    │   ├── routing.py                    # WebSocket routing
    │   ├── admin.py                      # Django Admin
    │   └── migrations/
    └── tests/
        ├── test_all.py                   # Automated test suite
        ├── debug_call.py                 # Twilio debug utility
        ├── download_ngrok.py             # ngrok installer
        └── ai_caller.postman_collection.json  # Postman collection
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Port 8000 in use` | `Get-NetTCPConnection -LocalPort 8000 \| % { Stop-Process -Id $_.OwningProcess -Force }` |
| `Voice calling disabled` | [Twilio Geo Permissions](https://console.twilio.com/us1/develop/voice/settings/geo-permissions) → Enable target country |
| `ElevenLabs 401` | Regenerate API key with `text_to_speech` permission at [elevenlabs.io](https://elevenlabs.io/app/settings/api-keys) |
| `Groq rate limit` | Wait 60 seconds and retry, or upgrade plan |
| `Deepgram connection fail` | Verify API key and [credit balance](https://console.deepgram.com) |
| `No audio on voice-test` | Use Chrome (Web Speech API required). Allow microphone. |
| `Caller hears silence` | Code includes `track: outbound` fix. Check ElevenLabs quota. |
| `ngrok tunnel expired` | Restart `python auto_ngrok.py` |
| `Render deploy fails` | Check `build.sh` has execute permission. Verify env vars set. |
| `daphne not found` | Activate venv: `.\venv\Scripts\activate` |

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit changes: `git commit -m "Add your feature"`
4. Push to branch: `git push origin feature/your-feature`
5. Open a Pull Request

Please ensure:
- All tests pass (`python tests/test_all.py`)
- Code follows Django conventions
- New endpoints have corresponding tests
- API changes are documented

---

## License

ISC License — See [LICENSE](LICENSE) for details.

---

## Disclaimer

This software is provided "as is" without warranty. The developers are not responsible for any misuse of this technology. Users must ensure compliance with all applicable telecommunications laws, privacy regulations, and ethical guidelines in their jurisdiction before deploying this system.

**AI-generated voice calls must comply with local regulations.** Always obtain proper consent and provide clear AI disclosure to call recipients.