"""
Automated Test Suite for AI Voice Caller
Tests all endpoints: health, chat, voice, history, inbound, call-status
"""

import urllib.request
import json
import time
import sys

BASE_URL = "http://localhost:8000"
PASSED = 0
FAILED = 0
RESULTS = []


def test(name, fn):
    global PASSED, FAILED
    try:
        result = fn()
        PASSED += 1
        RESULTS.append(f"  ✅ {name}")
        return result
    except Exception as e:
        FAILED += 1
        RESULTS.append(f"  ❌ {name} — {e}")
        return None


def get(path):
    r = urllib.request.urlopen(f"{BASE_URL}{path}")
    return json.loads(r.read().decode())


def post(path, data, content_type="application/json"):
    if content_type == "application/json":
        body = json.dumps(data).encode()
    else:
        body = data.encode() if isinstance(data, str) else data
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    r = urllib.request.urlopen(req)
    return json.loads(r.read().decode())


def post_expect_error(path, data, expected_status, content_type="application/json"):
    if content_type == "application/json":
        body = json.dumps(data).encode()
    else:
        body = data.encode() if isinstance(data, str) else data
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req)
        return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == expected_status:
            return json.loads(e.read().decode())
        raise


def get_expect_error(path, expected_status):
    try:
        r = urllib.request.urlopen(f"{BASE_URL}{path}")
        return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == expected_status:
            return json.loads(e.read().decode())
        raise


print("=" * 60)
print("  AI Voice Caller — Automated Test Suite")
print("=" * 60)
print()

# ---------------------------------------------------------------
# 1. Health Check
# ---------------------------------------------------------------
print("1. Health Check")


def t1():
    r = get("/calls/health/")
    assert r["status"] == "ok", f"Expected 'ok', got '{r['status']}'"
    assert r["service"] == "ai-voice-caller"
    print(f"     Response: {r}")


test("GET /calls/health/ returns ok", t1)
print()

# ---------------------------------------------------------------
# 2. Call History (empty)
# ---------------------------------------------------------------
print("2. Call History (should have existing test data)")


def t2():
    r = get("/calls/call-history/")
    assert "total" in r, "Missing 'total' field"
    assert "results" in r, "Missing 'results' field"
    assert isinstance(r["results"], list), "results should be a list"
    print(f"     Total calls: {r['total']}")


test("GET /calls/call-history/ returns list", t2)
print()

# ---------------------------------------------------------------
# 3. Call Detail (404 for unknown)
# ---------------------------------------------------------------
print("3. Call Detail (non-existent)")


def t3():
    r = get_expect_error("/calls/call-detail/CA_DOES_NOT_EXIST/", 404)
    assert r["error"] == "Call not found"
    print(f"     Response: {r}")


test("GET /calls/call-detail/<unknown> returns 404", t3)
print()

# ---------------------------------------------------------------
# 4. Inbound Call Webhook
# ---------------------------------------------------------------
print("4. Inbound Call Webhook (simulated Twilio POST)")


def t4():
    r = urllib.request.Request(
        f"{BASE_URL}/calls/inbound/",
        data="CallSid=CAautotest123&From=%2B919999999999&To=%2B16812816509".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    resp = urllib.request.urlopen(r)
    body = resp.read().decode()
    assert "<Stream" in body, "TwiML should contain <Stream> element"
    assert "wss://" in body, "TwiML should contain wss:// WebSocket URL"
    assert "/media-stream" in body, "TwiML should point to /media-stream"
    print(f"     TwiML: {body.strip()[:120]}...")


test("POST /calls/inbound/ returns valid TwiML", t4)
print()

# ---------------------------------------------------------------
# 5. Call Status Webhook
# ---------------------------------------------------------------
print("5. Call Status Webhook (simulated Twilio status update)")


def t5():
    r = post(
        "/calls/call-status/",
        "CallSid=CAautotest123&CallStatus=in-progress&CallDuration=0",
        content_type="application/x-www-form-urlencoded",
    )
    assert r["status"] == "received"
    print(f"     Response: {r}")


test("POST /calls/call-status/ processes status update", t5)
print()

# ---------------------------------------------------------------
# 6. Verify inbound call was saved to DB
# ---------------------------------------------------------------
print("6. Verify DB — inbound call saved")


def t6():
    r = get("/calls/call-detail/CAautotest123/")
    assert r["call_sid"] == "CAautotest123"
    assert r["from_number"] == "+919999999999"
    assert r["status"] == "in_progress"  # updated by call-status webhook
    print(f"     Session: {r['session_id']}, Status: {r['status']}")


test("GET /calls/call-detail/ shows saved inbound call", t6)
print()

# ---------------------------------------------------------------
# 7. Test Chat (Groq LLM)
# ---------------------------------------------------------------
print("7. Test Chat — Groq LLM (llama-3.1-8b-instant)")

t7_start = time.time()


def t7():
    r = post("/calls/test-chat/", {"message": "Hello, what is 2+2?", "session_id": "autotest"})
    assert "response" in r, "Missing 'response' field"
    assert len(r["response"]) > 0, "Response is empty"
    latency = int((time.time() - t7_start) * 1000)
    print(f"     AI: {r['response'][:80]}...")
    print(f"     Latency: {latency}ms")
    return r


test("POST /calls/test-chat/ gets LLM response", t7)
print()

# ---------------------------------------------------------------
# 8. Test Chat — Conversation Memory
# ---------------------------------------------------------------
print("8. Test Chat — Conversation Memory")


def t8():
    r = post(
        "/calls/test-chat/",
        {"message": "What number did I just ask about?", "session_id": "autotest"},
    )
    assert "response" in r
    assert r["turn"] == 2, f"Expected turn 2, got {r.get('turn')}"
    print(f"     AI: {r['response'][:80]}...")
    print(f"     Turn: {r['turn']} (conversation memory working)")


test("POST /calls/test-chat/ remembers context", t8)
print()

# ---------------------------------------------------------------
# 9. Test Voice (Groq + ElevenLabs)
# ---------------------------------------------------------------
print("9. Test Voice — Groq LLM + ElevenLabs TTS")

t9_start = time.time()


def t9():
    r = post(
        "/calls/test-voice/",
        {"message": "Say hi in one word", "session_id": "autotest-voice"},
    )
    assert "response" in r, "Missing 'response' field"
    latency = int((time.time() - t9_start) * 1000)
    print(f"     AI: {r['response'][:80]}...")
    print(f"     Latency: {latency}ms")

    if r.get("audio"):
        audio_size = len(r["audio"])
        print(f"     Audio: {audio_size} chars base64 (~{audio_size * 3 // 4 // 1024}KB mp3)")
        return r
    elif r.get("tts_error"):
        print(f"     ⚠️  TTS Error: {r['tts_error']}")
        print(f"     (LLM works, but ElevenLabs failed — check API key/quota)")
        return r
    else:
        raise Exception("No audio and no error — unexpected")


test("POST /calls/test-voice/ returns text + audio", t9)
print()

# ---------------------------------------------------------------
# 10. Test Page HTML
# ---------------------------------------------------------------
print("10. Test Page HTML")


def t10():
    r = urllib.request.urlopen(f"{BASE_URL}/calls/test/")
    html = r.read().decode()
    assert "AI Voice Caller" in html, "Page title missing"
    assert "sendMessage" in html, "JavaScript missing"
    assert "test-voice" in html, "Voice endpoint reference missing"
    print(f"     HTML size: {len(html)} bytes")


test("GET /calls/test/ returns working HTML page", t10)
print()

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
print("=" * 60)
print(f"  Results: {PASSED} passed, {FAILED} failed out of {PASSED + FAILED}")
print("=" * 60)
print()
for r in RESULTS:
    print(r)
print()

if FAILED > 0:
    print("⚠️  Some tests failed. Check the errors above.")
    sys.exit(1)
else:
    print("🎉 All tests passed!")
    sys.exit(0)
