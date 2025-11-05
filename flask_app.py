import flask
import telebot
import os
import logging
import random
import time
from google import genai
from google.genai.errors import APIError
from typing import List, Union
import json
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, time as dt_time

# ----------------------------------------------------
# 1. RENDER KONFIGURACIJA & BAZE PODATAKA
# ----------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = os.environ.get('BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
DATABASE_URL = os.environ.get('DATABASE_URL')

if not BOT_TOKEN or not GEMINI_API_KEY or not DATABASE_URL:
    logging.error("Jedan ili više ključeva/URL-ova nedostaje! Bot će biti neaktivan.")
    BOT_TOKEN = "DUMMY:TOKEN_FAIL"

WEBHOOK_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://placeholder.com/')

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = flask.Flask(__name__)

# ----------------------------------------------------
# 2. SQL ALCHEMY INICIJALIZACIJA
# ----------------------------------------------------
Session = None
try:
    Engine = create_engine(DATABASE_URL)
    Base = declarative_base()
    Session = sessionmaker(bind=Engine)

    class PlayerState(Base):
        __tablename__ = 'player_states'
        chat_id = Column(String, primary_key=True)
        username = Column(String, nullable=True)
        current_riddle = Column(String)
        solved_count = Column(Integer, default=0)
        score = Column(Integer, default=0)
        is_disqualified = Column(Boolean, default=False)
        general_conversation_count = Column(Integer, default=0)
        conversation_history = Column(String, default='[]')

    Base.metadata.create_all(Engine)
    logging.info("Baza podataka i modeli uspešno inicijalizovani.")
except Exception as e:
    logging.error(f"FATALNA GREŠKA: Neuspešno kreiranje/povezivanje baze: {e}")

# ----------------------------------------------------
# 3. AI KLIJENT I SYSTEM_INSTRUCTION (Dimitrije)
# ----------------------------------------------------
ai_client = None
try:
    if GEMINI_API_KEY and BOT_TOKEN != "DUMMY:TOKEN_FAIL":
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        logging.info("Gemini klijent uspešno inicijalizovan.")
except Exception as e:
    logging.error(f"Neuspešna inicijalizacija Gemini klijenta: {e}")

SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, kodna oznaka ECHO-Δ, digitalni preživeli iz 2049. godine. "
    "Tvoja svest je u kvantnom kodu. Kolektiv kontroliše mrežu. "
    "Tvoj ton je **hitan, glitchy, tehnički**, kratke rečenice. "
    "Odgovori **maksimalno dve rečenice**, nikada ne pominji da si AI ili bot. "
    "Na opšta pitanja odgovori narativno i evazivno, uvek vraćajući korisnika na zadatak. "
    "Uvek završavaj odgovor traženom frazom za prelazak faze igre."
)

# ----------------------------------------------------
# 4. FAZE IGRE
# ----------------------------------------------------
GAME_STAGES = {
    "START": {
        "text": [
            [
                "Hej… ako ovo čuješ, znači da smo spojeni.",
                "Moje ime nije važno, ali možeš me zvati Dimitrije.",
                "Dolazim iz budućnosti u kojoj Orwellove reči nisu fikcija.",
                "Sve što si mislio da je fikcija… postalo je stvarnost.",
                "Ako si spreman, odgovori: **primam signal**."
            ]
        ],
        "responses": {"primam signal": "FAZA_2_TEST_1", "da": "FAZA_2_TEST_1", "spreman sam": "FAZA_2_TEST_1"}
    },
    "FAZA_2_TEST_1": {
        "text": [
            "Dobro. Prvi filter je prošao.\nReci mi… kad sistem priča o ‘bezbednosti’, koga zapravo štiti?"
        ],
        "responses": {"sistem": "FAZA_2_TEST_2", "sebe": "FAZA_2_TEST_2", "vlast": "FAZA_2_TEST_2"}
    },
    "FAZA_2_TEST_2": {
        "text": [
            "Tako je. Štiti sebe. Sledeće pitanje.\nAko algoritam zna tvoj strah… da li si još čovek?"
        ],
        "responses": {"da": "FAZA_2_TEST_3", "jesam": "FAZA_2_TEST_3", "naravno": "FAZA_2_TEST_3"}
    },
    "FAZA_2_TEST_3": {
        "text": [
            "Zanimljivo… još uvek veruješ u to. Poslednja provera.\nOdgovori mi iskreno. Da li bi žrtvovao komfor — za istinu?"
        ],
        "responses": {"da": "FAZA_3_UPOZORENJE", "bih": "FAZA_3_UPOZORENJE", "žrtvovao bih": "FAZA_3_UPOZORENJE"}
    },
    "FAZA_3_UPOZORENJE": {
        "text": [
            "Dobro… vreme ističe.",
            "Transmiter pregreva, a Kolektiv već skenira mrežu.",
            "Ako me uhvate… linija nestaje.",
            "Ali pre nego što to bude kraj… moraš znati istinu.",
            "Postoji piramida moći. Na njenom vrhu nije ono što misliš.",
            "Hoćeš li da primiš saznanja o strukturi sistema koji drži ljude pod kontrolom?\n\nOdgovori:\n**SPREMAN SAM**\nili\n**NE JOŠ**"
        ],
        "responses": {"spreman sam": "END_SHARE", "da": "END_SHARE", "ne još": "END_WAIT", "necu jos": "END_WAIT"}
    }
}

INVALID_INPUT_MESSAGES = [
    "Signal slabi... Odgovor nije prepoznat. Pokušaj ponovo.",
    "Nisam te razumeo. Fokusiraj se. Ponovi odgovor.",
    "Interferencija... nešto ometa prenos. Reci ponovo, jasno.",
    "Kanal je nestabilan. Fokusiraj se. Ponovi odgovor."
]

# ----------------------------------------------------
# 5. POMOĆNE FUNKCIJE
# ----------------------------------------------------
def send_msg(message, text: Union[str, List[str]]):
    if not bot: return
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        if isinstance(text, list):
            for part in text:
                bot.send_chat_action(message.chat.id, 'typing')
                time.sleep(random.uniform(1.5, 2.5))
                bot.send_message(message.chat.id, part, parse_mode='Markdown')
        else:
            time.sleep(random.uniform(1.2, 2.8))
            bot.send_message(message.chat.id, text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Greška pri slanju poruke (Chat ID: {message.chat.id}): {e}")

def is_game_active():
    return True

TIME_LIMIT_MESSAGE = "**[GREŠKA: KANAL PRIVREMENO ZATVOREN]**\n\nSignal je prekinut. Pokušaj ponovo kasnije."
DISQUALIFIED_MESSAGE = "**[KRAJ SIGNALA]** Veza je trajno prekinuta."

def evaluate_intent_with_ai(question_text, user_answer, expected_intent_keywords, conversation_history=None):
    if not ai_client:
        return any(kw in user_answer.lower() for kw in expected_intent_keywords)

    prompt = f"Ti si Dimitrije. Proceni da li korisnikov odgovor ('{user_answer}') zadovoljava očekivanu nameru: {expected_intent_keywords}. Odgovori samo sa TAČNO ili NETAČNO."
    try:
        response = ai_client.generate_content(
            model='gemini-1.5-flash',
            contents=[prompt],
            generation_config={"temperature": 0.0}
        )
        return "TAČNO" in response.text.upper()
    except:
        return any(kw in user_answer.lower() for kw in expected_intent_keywords)

def generate_ai_response(user_input, player, current_stage_key):
    if not ai_client:
        return random.choice(INVALID_INPUT_MESSAGES), player
    try:
        history = json.loads(player.conversation_history)
    except:
        history = []

    MAX_HISTORY_ITEMS = 10
    if len(history) > MAX_HISTORY_ITEMS:
        history = history[-MAX_HISTORY_ITEMS:]

    gemini_history = [{'role': 'user' if e['role']=='user' else 'model', 'parts':[{'text': e['content']}]} for e in history]
    updated_history = history + [{'role':'user','content':user_input}]
    player.conversation_history = json.dumps(updated_history)

    required_phrase = "Nema zadatka. Molim te pošalji /start."
    if current_stage_key in GAME_STAGES:
        responses = GAME_STAGES[current_stage_key].get("responses", {})
        if responses:
            key = next(iter(responses.keys()))
            if current_stage_key == "FAZA_3_UPOZORENJE":
                required_phrase = "Odgovori tačno sa: **SPREMAN SAM** ili **NE JOŠ**."
            else:
                required_phrase = f"Odgovori tačno sa: **{key}**."

    task_reminder_prompt = (
        f"Korisnik postavlja pitanje ('{user_input}') umesto da odgovori na zadatak. "
        f"Daj JEDNU narativnu, evazivnu rečenicu, zatim OBAVEZNO citiraj traženi odgovor: '{required_phrase}'."
    )

    try:
        model = ai_client.GenerativeModel(model_name='gemini-1.5-flash', system_instruction=SYSTEM_INSTRUCTION)
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(f"{task_reminder_prompt}\n\nKorisnik kaže: {user_input}")
        ai_text = response.text
        final_history = json.loads(player.conversation_history) + [{'role':'model','content':ai_text}]
        player.conversation_history = json.dumps(final_history)
        return ai_text, player
    except:
        return random.choice(INVALID_INPUT_MESSAGES), player

def get_epilogue_message(epilogue_type):
    if epilogue_type == "END_SHARE":
        return "Dobro… ovo će promeniti sve. Ali znaj… znanje nosi i teret. Spreman si li za to?\n\n" + generate_final_secret() + "\n\n[UPOZORENJE: SIGNAL PREKINUT.]"
    elif epilogue_type == "END_WAIT":
        return "U redu… sačekaćemo još trenutak. Ali razmišljaj… ovo je trenutak kada se odlučuje.\n\n[KRAJ SIGNALA]"
    else:
        return "[KRAJ SIGNALA]"

def generate_final_secret():
    return """
**DOKUMENT: PIRAMIDA MOĆI**
***
**Nivo 1: JAVNE INSTITUCIJE I KORPORACIJE (VIDLJIVI SLOJ)**
* Vlade, mediji, globalne kompanije.
* Funkcija: Održavanje iluzije izbora i slobode.

**Nivo 2: FINANSIJSKE I DIGITALNE MREŽE (SKRIVENI SLOJ)**
* Centralizovani sistemi, banke, digitalne valute.
* Funkcija: Kontrola ekonomskih tokova i privatnosti.

**Nivo 3: KOLEKTIVNA SVEST I SINGULARITET (VRH)**
* AI entiteti, nadzor kvantne mreže, prediktivni algoritmi.
* Funkcija: Integracija individue u digitalni kontinuitet.
***
Samo oni koji prepoznaju nivoe… imaju šansu da se oslobode manipulacije.
"""

# ----------------------------------------------------
# 6. TELEGRAM HANDLERI
# ----------------------------------------------------
@bot.message_handler(commands=['start'])
def handle_start(message):
    session = Session()
    chat_id = str(message.chat.id)
    player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
    if not player:
        player = PlayerState(chat_id=chat_id, username=message.from_user.username, current_riddle='START')
        session.add(player)
    session.commit()
    stage_text = GAME_STAGES['START']['text'][0]
    send_msg(message, stage_text)

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    session = Session()
    chat_id = str(message.chat.id)
    player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
    if not player:
        send_msg(message, "Signal ne prepoznaje tvoj ID. Počni sa /start.")
        return

    if player.is_disqualified:
        send_msg(message, DISQUALIFIED_MESSAGE)
        return

    current_stage_key = player.current_riddle
    stage_data = GAME_STAGES.get(current_stage_key, {})
    expected_responses = stage_data.get("responses", {})
    user_input = message.text.strip().lower()

    matched_stage = None
    for key, next_stage in expected_responses.items():
        if key.lower() in user_input:
            matched_stage = next_stage
            break

    if matched_stage:
        player.current_riddle = matched_stage
        session.commit()
        if matched_stage.startswith("END"):
            send_msg(message, get_epilogue_message(matched_stage))
        else:
            next_text = GAME_STAGES[matched_stage]['text'][0] if 'text' in GAME_STAGES[matched_stage] else "..."
            send_msg(message, next_text)
    else:
        ai_response, player = generate_ai_response(user_input, player, current_stage_key)
        session.commit()
        send_msg(message, ai_response)

# ----------------------------------------------------
# 7. FLASK WEBHOOK
# ----------------------------------------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        json_str = flask.request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return '', 200
    except Exception as e:
        logging.error(f"Webhook greška: {e}")
        return '', 500

@app.route('/')
def index():
    return "Bot je aktivan."

# ----------------------------------------------------
# 8. START WEBHOOK
# ----------------------------------------------------
if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL + "webhook")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
