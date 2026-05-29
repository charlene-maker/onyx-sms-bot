"""
Onyx Cloud ↔ Twilio SMS Bridge
Receives inbound SMS via Twilio webhook, queries Onyx Cloud, replies via TwiML.
"""

import os
import requests
from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

ONYX_API_BASE = "https://cloud.onyx.app/api"
ONYX_API_KEY = os.environ["ONYX_API_KEY"]

# Maps phone number → Onyx chat_session_id so each caller keeps their history.
# Resets when the server restarts. For persistence, swap this for a small DB.
sessions: dict[str, str] = {}


def ask_onyx(message: str, phone: str) -> str:
    headers = {
        "Authorization": f"Bearer {ONYX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict = {"message": message, "stream": False}

    # Re-use existing session if we have one for this number
    if phone in sessions:
        payload["chat_session_id"] = sessions[phone]

    resp = requests.post(
        f"{ONYX_API_BASE}/chat/send-chat-message",
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # Store session ID so follow-up texts continue the same conversation
    if data.get("chat_session_id"):
        sessions[phone] = str(data["chat_session_id"])

    return data.get("answer_citationless") or data.get("answer", "Sorry, no response.")


@app.route("/sms", methods=["POST"])
def sms_reply():
    body = request.form.get("Body", "").strip()
    from_number = request.form.get("From", "unknown")

    # Let caller reset their conversation by texting "reset"
    if body.lower() == "reset":
        sessions.pop(from_number, None)
        answer = "Conversation reset. Ask me anything!"
    else:
        try:
            answer = ask_onyx(body, from_number)
        except Exception as e:
            app.logger.error("Onyx error: %s", e)
            answer = "Something went wrong. Please try again."

    # Truncate to SMS limit (1600 chars to be safe)
    if len(answer) > 1600:
        answer = answer[:1597] + "..."

    twiml = MessagingResponse()
    twiml.message(answer)
    return Response(str(twiml), mimetype="application/xml")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
