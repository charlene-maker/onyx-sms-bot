"""
Onyx Cloud ↔ Twilio SMS Bridge
Receives inbound SMS via Twilio webhook, queries Onyx Cloud, replies via Twilio REST API.
Responds immediately to Twilio to avoid 15s timeout, then sends Onyx answer as a follow-up.
"""

import os
import threading
import requests
from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client

app = Flask(__name__)

ONYX_API_BASE = "https://cloud.onyx.app/api"
ONYX_API_KEY = os.environ["ONYX_API_KEY"]
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Maps phone number → Onyx chat_session_id so each caller keeps their history.
sessions: dict[str, str] = {}


def ask_onyx(message: str, phone: str) -> str:
    headers = {
        "Authorization": f"Bearer {ONYX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict = {
        "message": message,
        "stream": False,
        "chat_session_info": {"persona_id": 1},
    }

    if phone in sessions:
        payload["chat_session_id"] = sessions[phone]

    resp = requests.post(
        f"{ONYX_API_BASE}/chat/send-chat-message",
        headers=headers,
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("chat_session_id"):
        sessions[phone] = str(data["chat_session_id"])

    return data.get("answer_citationless") or data.get("answer", "Sorry, no response.")


def process_and_reply(body: str, from_number: str, to_number: str):
    """Runs in background thread — calls Onyx then sends reply via Twilio REST API."""
    try:
        if body.lower() == "reset":
            sessions.pop(from_number, None)
            answer = "Conversation reset. Ask me anything!"
        else:
            answer = ask_onyx(body, from_number)
    except Exception as e:
        app.logger.error("Onyx error: %s", e)
        answer = "Something went wrong. Please try again."

    # Truncate to SMS limit (Twilio counts bytes, not chars — use 1500 to be safe)
    encoded = answer.encode("utf-8")
    if len(encoded) > 1500:
        answer = encoded[:1497].decode("utf-8", errors="ignore") + "..."

    twilio_client.messages.create(
        body=answer,
        from_=to_number,
        to=from_number,
    )


@app.route("/sms", methods=["POST"])
def sms_reply():
    body = request.form.get("Body", "").strip()
    from_number = request.form.get("From", "unknown")
    to_number = request.form.get("To", "")

    # Immediately acknowledge Twilio to avoid timeout
    threading.Thread(
        target=process_and_reply,
        args=(body, from_number, to_number),
        daemon=True,
    ).start()

    # Return empty TwiML — real reply comes via REST API above
    return Response(str(MessagingResponse()), mimetype="application/xml")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
