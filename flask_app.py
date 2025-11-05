import flask
import telebot
import os
import logging
import random
import time
import json
from google import genai
from google.genai.errors import APIError
from typing import List, Union
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from telebot.apihelper import ApiTelegramException # Dodajemo za bolju detekciju grešaka

# ----------------------------------------------------
# 2. KONFIGURACIJA I INICIJALIZACIJA
# ----------------------------------------------------

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = os.environ.get('BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
DATABASE_URL = os.environ.get('DATABASE_URL')

# Stroga provera i postavljanje Sigurnosnog prekidača
if not BOT_TOKEN:
    logging.critical("KRITIČNA GREŠKA: BOT_TOKEN nije postavljen.")
    BOT_TOKEN = "DUMMY:TOKEN_FAIL" 

WEBHOOK_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://placeholder.com/')

# Inicijalizacija bot objekta mora biti uspešna
try:
    bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
except Exception as e:
    logging.critical(f"FATALNO: Neuspešna inicijalizacija telebot objekta: {e}")
    # Ako ovde padne, Python aplikacija je srušena
    raise e 

app = flask.Flask(__name__)

# ----------------------------------------------------
# 3. SQL ALCHEMY INICIJALIZACIJA (Robusna)
# ----------------------------------------------------

Session = None
try:
    if DATABASE_URL:
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
    else:
        logging.warning("DATABASE_URL nedostaje. Aplikacija se pokreće, ali stanje neće biti sačuvano.")

except Exception as e:
    logging.error(f"FATALNA GREŠKA: Neuspešno kreiranje/povezivanje baze. Aplikacija će raditi bez DB sesije. Greška: {e}") 
    Session = None

# ----------------------------------------------------
# 4. AI KLIJENT I DATA (Robusna)
# ----------------------------------------------------

GEMINI_MODEL_NAME = 'gemini-2.5-flash' 
ai_client = None

if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        logging.info("Gemini klijent uspešno inicijalizovan.")
    except Exception as e:
        logging.error(f"Neuspešna inicijalizacija Gemini klijenta. Bot će koristiti Fallback. Greška: {e}")
else:
     logging.warning("GEMINI_API_KEY nedostaje. Bot će koristiti samo hardkodovane odgovore.")

# ... (SYSTEM_INSTRUCTION i GAME_STAGES ostaju isti kao u V5.1) ...
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, član pokreta otpora iz 2049. godine. Svet je pod kontrolom entiteta 'Kolektiv'. Komuniciraš sa korisnikom preko nestabilnog kvantnog transmitera. Tvoj cilj je da testiraš korisnika da bi primio dokument o strukturi moći. "
    "Tvoj ton je **hitan, direktan, pomalo tehnički i 'glitchy'**. Govoriš kratko i jasno. Vreme je ključno. "
    "**KRITIČNO: Tvoji odgovori moraju biti kratki, najviše DVE rečenice.** Svaka sekunda je rizik. "
    "**Nikada ne pominji da si AI.** Ti si osoba iz budućnosti. Nikada ne traži od korisnika da kuca komande. "
    "**REAKCIJE NA UPIT (Kada ignoriše zadatak):** Ako korisnik postavi irelevantno pitanje ('ko si ti?', 'šta hoćeš?'), **nikada ne odgovaraj direktno na to pitanje.** Umesto toga, odgovori kratkim, narativnim komentarom koji **pojačava narativ opasnosti/pritiska** i **vraća fokus** na poslednji zadatak. Ne koristi frazu zadatka, samo narativni komentar. "
    "Primer: 'Kolektiv skenira mrežu. Nema vremena za lične priče. Fokusiraj se na ono što je ispred tebe.' "
)

GAME_STAGES = {
    "START": {
        "text": [ 
            [
                "Hej… ako ovo čuješ, znači da smo spojeni.",
                "Moje ime nije važno, ali možeš me zvati Dimitrije.",
                "Dolazim iz budućnosti u kojoj Orwellove reči nisu fikcija.",
                "Sve što si mislio da je fikcija… postalo je stvarnost.",
                "Ako si spreman, odgovori: **primam signal**."
            ],
            [
                "Signal je prošao… čuješ li me?",
                "Zovi me Dimitrije. Ja sam eho iz sveta koji dolazi.",
                "Svet koji ste vi samo zamišljali, mi živimo. I nije utopija.",
                "Trebam tvoju pomoć. Ali prvo moram da znam da li si na pravoj strani.",
                "Ako si tu, reci: **primam signal**."
            ],
            [
                "Kvantni tunel je otvoren. Veza je nestabilna, ali drži.",
                "Ja sam Dimitrije. Govorim ti iz 2049. godine.",
                "Sve ono čega ste se plašili... desilo se. Kolektiv kontroliše sve.",
                "Tražim saveznike u prošlosti. U tvom vremenu. Jesi li ti jedan od njih?",
                "Odgovori sa: **primam signal**."
            ]
        ],
        "responses": {"primam signal": "FAZA_2_TEST_1", "da": "FAZA_2_TEST_1", "spreman sam": "FAZA_2_TEST_1"}
    },
    "FAZA_2_TEST_1": {
        "text": [ 
            "Dobro. Prvi filter je prošao.\nReci mi… kad sistem priča o ‘bezbednosti’, koga zapravo štiti?",
            "U redu. Prošao si prvu proveru.\nSledeće pitanje: Kad sistem obećava ‘bezbednost’, čije interese on zaista čuva?",
            "Signal je stabilan. Idemo dalje.\nRazmisli o ovome: Kada čuješ reč ‘bezbednost’ od sistema, koga on štiti?"
        ],
        "responses": {"sistem": "FAZA_2_TEST_2", "sebe": "FAZA_2_TEST_2", "vlast": "FAZA_2_TEST_2"}
    },
    "FAZA_2_TEST_2": {
        "text": [ 
            "Tako je. Štiti sebe. Sledeće pitanje.\nAko algoritam zna tvoj strah… da li si još čovek?",
            "Da. Sebe. To je ključno. Idemo dalje.\nAko mašina predviđa tvoje želje pre tebe... da li su te želje i dalje tvoje?",
            "Tačno. Njegova prva briga je sam za sebe. Sledeće.\nAko algoritam zna tvoj strah… da li si još uvek slobodan?"
        ],
        "responses": {"da": "FAZA_2_TEST_3", "jesam": "FAZA_2_TEST_3", "naravno": "FAZA_2_TEST_3"}
    },
    "FAZA_2_TEST_3": {
        "text": [ 
            "Zanimljivo… još uvek veruješ u to. Poslednja provera.\nOdgovori mi iskreno. Da li bi žrtvovao komfor — za istinu?",
            "Držiš se za tu ideju... Dobro. Finalno pitanje.\nDa li bi menjao udobnost neznanja za bolnu istinu?",
            "To je odgovor koji sam očekivao. Poslednji test.\nReci mi, da li je istina vredna gubljenja sigurnosti?"
        ],
        "responses": {"da": "FAZA_3_UPOZORENJE", "bih": "FAZA_3_UPOZORENJE", "žrtvovao bih": "FAZA_3_UPOZORENJE", "zrtvovao bih": "FAZA_3_UPOZORENJE"}
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
# ----------------------------------------------------
# 5. POMOĆNE FUNKCIJE I KONSTANTE (V5.2)
# ----------------------------------------------------

# ... (Sve pomoćne funkcije ostaju iste - get_required_phrase, send_msg, evaluate_intent_with_ai, generate_ai_response) ...

# ----------------------------------------------------
# 6. WEBHOOK RUTE (V5.2 - Potvrda da radi)
# ----------------------------------------------------

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if flask.request.headers.get('content-type') == 'application/json':
        json_string = flask.request.get_data().decode('utf-8')

        if BOT_TOKEN == "DUMMY:TOKEN_FAIL":
            logging.error("Telegram Webhook pozvan, ali BOT_TOKEN je neispravan. Proverite Render.")
            return "Bot token nije konfigurisan.", 200

        try:
             update = telebot.types.Update.de_json(json_string)
        except Exception as e:
             logging.error(f"Greška pri parsiranju JSON-a: {e}")
             return ''

        if update.message or update.edited_message or update.callback_query:
            bot.process_new_updates([update])

        return ''
    else:
        flask.abort(403)

@app.route('/set_webhook', methods=['GET'])
def set_webhook_route():
    if BOT_TOKEN == "DUMMY:TOKEN_FAIL":
        return "Failed: BOT_TOKEN nije postavljen.", 200

    webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
    
    try:
        bot.remove_webhook() 
        s = bot.set_webhook(url=webhook_url_with_token)
        if s:
            return f"Webhook successfully set to: {webhook_url_with_token}! Bot je spreman. Pošaljite /start!"
        else:
             return f"Failed to set webhook. Telegram API odbio zahtev. URL: {webhook_url_with_token}"
    except ApiTelegramException as e:
        return f"CRITICAL TELEGRAM API ERROR: {e}. Proverite TOKEN i URL."
    except Exception as e:
        return f"CRITICAL PYTHON ERROR: {e}"

# ----------------------------------------------------
# 7. BOT HANDLERI (V5.2 - Logika faza je sada stabilna)
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'pokreni'])
def handle_commands(message):
    if Session is None: 
        send_msg(message, "GREŠKA: Trajno stanje (DB) nije dostupno. Igru možete započeti, ali napredak neće biti sačuvan. Pošaljite bilo šta za početak.")
        # Omogućavamo igraču da nastavi, iako bez trajnog stanja (DB)
        if message.text.lower() in ['/start', 'start']:
            start_message = random.choice(random.choice(GAME_STAGES["START"]["text"]))
            send_msg(message, start_message)
        return

    # ... (Ostatak logike sa Session ostaje kao u V5.1) ...

@bot.message_handler(func=lambda message: not message.text.startswith('/'))
def handle_general_message(message):
    if Session is None: 
        # Ako je DB nedostupan, ne radimo ništa, jer ne možemo pratiti faze
        return

    # ... (Logika faza ostaje ista, jer je V5.0 ispravila glavni bug) ...

# ----------------------------------------------------
# 8. POKRETANJE APLIKACIJE (V5.2 - Fokus na Webhook)
# ----------------------------------------------------

if __name__ != '__main__':
    if BOT_TOKEN != "DUMMY:TOKEN_FAIL":
        webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
        
        try:
            bot.remove_webhook()
            success = bot.set_webhook(url=webhook_url_with_token)
            
            if success:
                 logging.info(f"Webhook uspešno postavljen: {webhook_url_with_token}")
            else:
                 logging.error(f"Neuspešno postavljanje Webhooka. Telegram API odbio zahtev.")
        except ApiTelegramException as e:
            logging.critical(f"Kritična greška pri postavljanju Webhooka (API): {e}. Proverite token.")
        except Exception as e:
            logging.critical(f"Kritična nepoznata greška pri postavljanju Webhooka: {e}")
    else:
        logging.critical("Webhook inicijalizacija preskočena jer BOT_TOKEN nedostaje. Proverite Render.")
