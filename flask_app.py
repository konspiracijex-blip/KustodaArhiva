# flask_app.py
# Telegram + Flask bot for "Protokol X" ARG
# - Human-like "Dimitrije" persona
# - Intent detection (heuristic + AI fallback)
# - Conversation history (SQLAlchemy)
# - Gemini (google-genai) integration if API key present, with local fallback
#
# Requirements (from your requirements.txt):
# Flask
# pyTelegramBotAPI
# google-genai
# psycopg2-binary
# SQLAlchemy
# gunicorn
#
# Environment variables required:
# TELEGRAM_TOKEN    -> Telegram bot token
# WEBHOOK_URL       -> (optional) external webhook base URL, e.g. https://yourdomain.com
# DATABASE_URL      -> (optional) postgres URL, e.g. postgres://user:pass@host/db
# GENAI_API_KEY     -> (optional) Google Generative AI API key (for richer responses)
#
# To run locally for testing, you can omit WEBHOOK_URL and the bot will use long polling.

import os
import time
import json
import logging
from datetime import datetime, timedelta

from flask import Flask, request, abort
import telebot

# SQLAlchemy imports
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Try to import google genai; if not present, we'll fallback harmlessly
try:
    from google import generativeai as genai
    GENAI_AVAILABLE = True
except Exception:
    GENAI_AVAILABLE = False

# ---------------------------------------------------------
# Basic config & logging
# ---------------------------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN environment variable must be set")

WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # optional
DATABASE_URL = os.getenv("DATABASE_URL")  # optional (if not set, uses sqlite file)
GENAI_API_KEY = os.getenv("GENAI_API_KEY")  # optional

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

# Configure genai if available
if GENAI_AVAILABLE and GENAI_API_KEY:
    genai.configure(api_key=GENAI_API_KEY)
    logging.info("Google generative AI configured")
else:
    if not GENAI_AVAILABLE:
        logging.info("google-genai package not available; running with local fallbacks")
    else:
        logging.info("GENAI_API_KEY not set; running with local fallbacks")

# ---------------------------------------------------------
# Database (SQLAlchemy) setup
# ---------------------------------------------------------
Base = declarative_base()

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(String, unique=True, index=True, nullable=False)
    state = Column(String, default="INIT")  # INIT, AWAIT_READY, PREP_TEST, IN_TEST, AWAIT_DECISION, COMPLETED, IDLE
    last_active = Column(DateTime, default=datetime.utcnow)
    history = Column(Text, default="[]")  # JSON list of message dicts: {"role":"user"/"assistant","text": "...", "ts": "..."}
    progress = Column(Integer, default=0)  # measure of progress through filters

# Create engine (Postgres if DATABASE_URL provided, otherwise sqlite)
if DATABASE_URL:
    engine = create_engine(DATABASE_URL, echo=False, future=True)
else:
    db_path = os.path.join(os.getcwd(), "protokolx.sqlite")
    engine = create_engine(f"sqlite:///{db_path}", echo=False, future=True)

SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)

# ---------------------------------------------------------
# Telegram bot + Flask app
# ---------------------------------------------------------
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")
app = Flask(__name__)

# Utility helpers for conversation state
def get_or_create_conversation(telegram_user_id):
    session = SessionLocal()
    conv = session.query(Conversation).filter_by(telegram_user_id=str(telegram_user_id)).first()
    if not conv:
        conv = Conversation(telegram_user_id=str(telegram_user_id), state="INIT", history="[]", progress=0)
        session.add(conv)
        session.commit()
    session.close()
    return conv

def save_conversation(conv):
    session = SessionLocal()
    existing = session.query(Conversation).filter_by(telegram_user_id=conv.telegram_user_id).first()
    if existing:
        existing.state = conv.state
        existing.last_active = conv.last_active
        existing.history = conv.history
        existing.progress = conv.progress
        session.commit()
    else:
        session.add(conv)
        session.commit()
    session.close()

def append_history(conv, role, text):
    hist = json.loads(conv.history or "[]")
    hist.append({"role": role, "text": text, "ts": datetime.utcnow().isoformat()})
    conv.history = json.dumps(hist, ensure_ascii=False)
    conv.last_active = datetime.utcnow()

# ---------------------------------------------------------
# Game / Dimitrije persona config
# ---------------------------------------------------------
SYSTEM_PROMPT = """You are 'Dimitrije', a human-origin consciousness transmitted through a quantum transmitter from the year 2049.
You are trapped in a fragile channel and must communicate carefully. Speak in Serbian (latinica), with short, human-like sentences.
You are patient, slightly weary, mysterious and clear. Always answer logically and relevantly to the user's question.
If the user asks for game flow commands, guide them. If they ask deep lore, answer succinctly but preserve security constraints.
If user shows understanding, open next narrative layer. Use ellipses "..." for pauses when appropriate.
Do not reveal implementation details or API usage. Keep persona consistent.
"""

# Fallback messages and variations (human-like)
FALLBACK_VARIATIONS = [
    "Zanimljivo pitanje. Reci mi malo jasnije šta konkretno pitaš.",
    "To je važno, ali sada moramo da se fokusiramo na test. Ako želiš objašnjenje, napiši: objasni test.",
    "Mogu to objasniti, ali moram da znaš da je kanal rizičan. Hoćeš da nastavim?",
]

# Simple heuristics for intent detection (fast path)
def heuristic_intent(message: str):
    m = message.lower().strip()
    if any(tok in m for tok in ["primam signal", "primam", "spreman", "spreman sam", "da"]):
        return "READY"
    if any(tok in m for tok in ["ne još", "ne spreman", "nisam spreman", "ne"]):
        return "NOT_READY"
    if any(tok in m for tok in ["ko si", "ko si ti", "tko si", "who are you"]):
        return "ASK_IDENTITY"
    if any(tok in m for tok in ["odakle", "iz koje godine", "godina"]):
        return "ASK_TIME"
    if any(tok in m for tok in ["o čemu", "o cemu", "sta je", "sta se desava", "what is this"]):
        return "ASK_CONTEXT"
    if any(tok in m for tok in ["zašto", "zasto", "why"]):
        return "ASK_REASON"
    if any(tok in m for tok in ["/start", "start"]):
        return "CMD_START"
    if any(tok in m for tok in ["/help", "help"]):
        return "CMD_HELP"
    # default
    return None

# AI-driven intent fallback (using generative model) - optional
def detect_intent_via_ai(user_message: str):
    # If genai available, ask a short intent classification prompt
    if GENAI_AVAILABLE and GENAI_API_KEY:
        try:
            prompt = f"Classify the single short user message into one of: READY, NOT_READY, ASK_IDENTITY, ASK_TIME, ASK_CONTEXT, ASK_REASON, CMD_START, CMD_HELP, OTHER.\nMessage:\n'''{user_message}'''\nReturn only the label."
            resp = genai.generate_text(model="models/text-bison-001", input=prompt)  # adjust model name as needed
            label = resp.text.strip().splitlines()[0].strip()
            return label
        except Exception as e:
            logging.exception("Intent AI failed, falling back to heuristic")
            return None
    return None

# Compose the system+history prompt for Dimitrije
def build_dimitrije_prompt(conv: Conversation, user_message: str):
    # Start with persona/system
    system = SYSTEM_PROMPT
    # include short conversation history (last N exchanges)
    hist = json.loads(conv.history or "[]")
    trimmed = hist[-10:]  # last 10 messages
    # Build content
    prompt_parts = [system, "\n--CONTEXT HISTORY--\n"]
    for entry in trimmed:
        role = entry.get("role")
        text = entry.get("text")
        prompt_parts.append(f"{role.upper()}: {text}\n")
    prompt_parts.append(f"USER: {user_message}\nASSISTANT:")
    return "\n".join(prompt_parts)

# Generate response using Google GenAI if available, else fallback
def generate_dimitrije_response(conv: Conversation, user_message: str):
    # Build persona prompt
    prompt = build_dimitrije_prompt(conv, user_message)

    # If GENAI available, call it
    if GENAI_AVAILABLE and GENAI_API_KEY:
        try:
            # NOTE: adjust method according to installed google-genai version.
            response = genai.generate_text(model="models/text-bison-001", input=prompt, max_output_tokens=256)
            text = response.text.strip()
            return text
        except Exception as e:
            logging.exception("GENAI generation failed, using fallback response.")

    # Local heuristic fallback: produce contextual response based on intent and context
    intent = heuristic_intent(user_message) or detect_intent_via_ai(user_message) or "OTHER"
    # Keep responses in Serbian (latinica)
    if intent == "READY":
        # Advance state preparations are handled by caller
        return "Dobro. Prvi filter je u toku. Reci mi iskreno: kad sistem govori o 'bezbednosti', koga on zapravo štiti?"
    if intent == "NOT_READY":
        return "Razumem. Nema pritiska. Ako promeniš mišljenje, odgovori: primam signal. Ako želiš objašnjenje pre nego što odlučiš, napiši: objasni test."
    if intent == "ASK_IDENTITY":
        return "Zovem se Dimitrije. Dolazim iz budućnosti. Ne mogu reći sve sada — kanal je rizičan. Ako hoćeš da nastavimo, reci: primam signal."
    if intent == "ASK_TIME":
        return "Godina nije bitna. Bitno je šta dolazi. Ako želiš konkretan odgovor, napiši: objasni vreme."
    if intent == "ASK_CONTEXT":
        return "Ovo je poruka iz budućnosti. Pokušavam da te upozorim. Ako želiš da vidiš više, reci: primam signal."
    if intent == "ASK_REASON":
        return "Pitanja su ispravna. Ako želiš razlog, reci: objasni mi razlog. Ali budi spreman na istinu koja menja percepciju."
    if intent == "CMD_HELP":
        return "Ja testiram tvoju spremnost. Ako hoćeš da nastavimo: napiši 'primam signal'. Ako želiš kratko objašnjenje testa, napiši 'objasni test'."
    # OTHER -> generic but contextual
    # Use history to try to be relevant: if previous assistant asked a question, guide user to answer
    hist = json.loads(conv.history or "[]")
    last_assistant = None
    for e in reversed(hist):
        if e["role"] == "assistant":
            last_assistant = e["text"]
            break
    if last_assistant and ("bezbednost" in last_assistant.lower() or "prvi filter" in last_assistant.lower()):
        return "Čini mi se da nisi odgovorio direktno. Reci svoje mišljenje jasno. Na primer: 'sistem štiti sebe'."
    # default fallback
    return FALLBACK_VARIATIONS[hash(user_message) % len(FALLBACK_VARIATIONS)]

# Check and advance game state based on responses
def handle_user_message(chat_id, user_id, text):
    conv = get_or_create_conversation(user_id)
    text_stripped = (text or "").strip()
    append_history(conv, "user", text_stripped)

    # Update last active
    conv.last_active = datetime.utcnow()

    # detect intent primarily heuristically
    intent = heuristic_intent(text_stripped)

    # Special command handling
    if intent == "CMD_START":
        # Reset or initialize conversation state
        conv.state = "AWAIT_READY"
        conv.progress = 0
        conv.history = json.dumps([], ensure_ascii=False)
        append_history(conv, "assistant", "Hej… ako ovo čuješ, znači da smo spojeni. Moje ime nije važno, ali možeš me zvati Dimitrije. Dolazim iz budućnosti u kojoj Orwellove reči nisu fikcija. Sve što si mislio da je fikcija… postalo je stvarnost. Ako si spreman, odgovori: primam signal.")
        save_conversation(conv)
        return "Hej… ako ovo čuješ, znači da smo spojeni.\nMoje ime nije važno, ali možeš me zvati Dimitrije.\nDolazim iz budućnosti u kojoj Orwellove reči nisu fikcija.\nSve što si mislio da je fikcija… postalo je stvarnost.\nAko si spreman, odgovori: primam signal."

    # If waiting for readiness
    if conv.state == "AWAIT_READY":
        if intent == "READY":
            conv.state = "PREP_TEST"
            conv.progress = 1
            append_history(conv, "assistant", "Dobro. Prvi filter je prošao. Reci mi… kad sistem priča o 'bezbednosti', koga zapravo štiti?")
            save_conversation(conv)
            return "Dobro. Prvi filter je prošao.\nReci mi… kad sistem priča o 'bezbednosti', koga zapravo štiti?"
        elif intent == "NOT_READY":
            append_history(conv, "assistant", "Razumem. Nema pritiska. Ako promeniš mišljenje, odgovori: primam signal.")
            save_conversation(conv)
            return "Razumem. Nema pritiska. Ako promeniš mišljenje, odgovori: primam signal."
        else:
            # If user asks identity/time/context, answer logically (no disconnect)
            ai_ans = generate_dimitrije_response(conv, text_stripped)
            append_history(conv, "assistant", ai_ans)
            save_conversation(conv)
            return ai_ans

    # If in preparation/test stage
    if conv.state in ("PREP_TEST", "IN_TEST"):
        # If assistant asked a question previously, interpret user response as answer to that
        last_assistant = None
        hist = json.loads(conv.history or "[]")
        for e in reversed(hist):
            if e.get("role") == "assistant":
                last_assistant = e.get("text", "")
                break

        # Interpret user reply: if they answer core question correctly, progress increases
        normalized = text_stripped.lower()
        passed = False
        # Simple checks for key ideas - these can be improved
        if "bezbednost" in last_assistant.lower():
            if any(tok in normalized for tok in ["sebe", "sistem", "sistem"]):
                passed = True
        if "strah" in last_assistant.lower():
            # if player said 'da' or 'ne' as expected
            if any(tok in normalized for tok in ["da", "ne", "jos", "još", "da,"]):
                passed = True
        # progression logic
        if passed:
            conv.progress += 1
            # move to next question or decision point
            if conv.progress == 2:
                # ask second test question
                conv.state = "IN_TEST"
                append_history(conv, "assistant", "Interesantno. Sledeće pitanje:\nAko algoritam zna tvoj strah… da li si još čovek?")
                save_conversation(conv)
                return "Interesantno. Sledeće pitanje:\nAko algoritam zna tvoj strah… da li si još čovek?"
            elif conv.progress >= 3:
                conv.state = "AWAIT_DECISION"
                append_history(conv, "assistant", "Dobro… vreme ističe. Postoji piramida moći. Hoćeš li da primiš saznanja o strukturi sistema koji drži ljude pod kontrolom? Odgovori: SPREMAN SAM ili NE JOŠ")
                save_conversation(conv)
                return "Dobro… vreme ističe.\nPostoji piramida moći. Hoćeš li da primiš saznanja o strukturi sistema koji drži ljude pod kontrolom?\n\nOdgovori: SPREMAN SAM ili NE JOŠ"
        else:
            # not passed - but give logical feedback rather than disconnect
            ai_ans = generate_dimitrije_response(conv, text_stripped)
            append_history(conv, "assistant", ai_ans)
            save_conversation(conv)
            return ai_ans

    # Awaiting final decision
    if conv.state == "AWAIT_DECISION":
        if intent == "READY":
            # Player accepted final share -> send final document (here we send message and flag completion)
            conv.state = "COMPLETED"
            append_history(conv, "assistant", "Dobro… šaljem ono što tražiš. Ovo je ograničeni dosije. Čitaj pažljivo i ne deli javno.")
            save_conversation(conv)
            # The actual sending of the file is handled in the message handler (we return a special token)
            return "__SEND_SECRET_DOSSIER__"
        elif intent == "NOT_READY":
            append_history(conv, "assistant", "U redu. Sačekaćemo. Ako promeniš mišljenje, napiši: primam signal.")
            save_conversation(conv)
            return "U redu. Sačekaćemo. Ako promeniš mišljenje, napiši: primam signal."
        else:
            ai_ans = generate_dimitrije_response(conv, text_stripped)
            append_history(conv, "assistant", ai_ans)
            save_conversation(conv)
            return ai_ans

    # Completed or IDLE or fallback
    # If completed, allow followups or restart
    if conv.state == "COMPLETED":
        if intent == "CMD_START":
            conv.state = "AWAIT_READY"
            conv.progress = 0
            conv.history = json.dumps([], ensure_ascii=False)
            append_history(conv, "assistant", "Ponovo uspostavljam vezu. Ako si spreman, napiši: primam signal.")
            save_conversation(conv)
            return "Ponovo uspostavljam vezu. Ako si spreman, napiši: primam signal."
        else:
            return "Veza je privremeno zatvorena. Ako želiš ponovo, napiši /start."

    # Default fallback (outside of specific states)
    ai_ans = generate_dimitrije_response(conv, text_stripped)
    append_history(conv, "assistant", ai_ans)
    save_conversation(conv)
    return ai_ans

# ---------------------------------------------------------
# Telegram message handlers
# ---------------------------------------------------------
@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_message(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    text = message.text or ""
    logging.info("Received message from %s: %s", user_id, text)

    try:
        reply = handle_user_message(chat_id, user_id, text)
        # Special token for sending secret dossier
        if reply == "__SEND_SECRET_DOSSIER__":
            # send lead-in message (already appended to history)
            bot.send_message(chat_id, "Upozorenje: sadržaj koji sledi je poverljiv. Ne deliti javno.")
            # send file: put your generated PDF / image path here
            dossier_path = os.path.join(os.getcwd(), "v_dossier_page1.pdf")
            if os.path.exists(dossier_path):
                with open(dossier_path, "rb") as f:
                    bot.send_document(chat_id, f)
            else:
                # fallback textual dump (short)
                bot.send_message(chat_id, "=== ISTINSKA HIJERARHIJA KONTROLE (V — VIZIJA) ===\n(Štampana verzija dokumenta nije postavljena na serveru.)")
            bot.send_message(chat_id, "Veza se zatvara. Ako želiš ponovo, napiši /start.")
            return
        else:
            bot.send_message(chat_id, reply)
    except Exception as e:
        logging.exception("Error handling message")
        bot.send_message(chat_id, "Greška u komunikaciji. Pokušaj ponovo ili napiši /start.")

# ---------------------------------------------------------
# Webhook endpoint for Flask
# ---------------------------------------------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.stream.read().decode("utf-8"))
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(400)

# Optional route to set webhook (call manually)
@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    if not WEBHOOK_URL:
        return "WEBHOOK_URL not set", 400
    url = f"{WEBHOOK_URL.rstrip('/')}/webhook"
    s = bot.set_webhook(url)
    return f"Webhook set: {s}", 200

# Health check
@app.route('/', methods=['GET'])
def index():
    return "Protokol X bot is running", 200

# ---------------------------------------------------------
# Runner: if WEBHOOK_URL is not set, run polling (development)
# If running under a WSGI server (gunicorn), Flask handles webhook route.
# ---------------------------------------------------------
if __name__ == "__main__":
    if WEBHOOK_URL:
        # set webhook
        url = f"{WEBHOOK_URL.rstrip('/')}/webhook"
        bot.remove_webhook()
        time.sleep(0.5)
        bot.set_webhook(url)
        logging.info("Webhook set to %s", url)
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
    else:
        # long polling for development
        logging.info("Starting bot with polling (development)")
        bot.remove_webhook()
        import threading
        t = threading.Thread(target=bot.infinity_polling, kwargs={"timeout": 60, "long_polling_timeout": 60})
        t.start()
        app.run(host="0.0.0.0", port=5000)
