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
from telebot.apihelper import ApiTelegramException 

# ----------------------------------------------------
# 2. KONFIGURACIJA I INICIJALIZACIJA
# ----------------------------------------------------

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# OBAVEZNO: Podesite ove promenljive u vašem okruženju (Render)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
DATABASE_URL = os.environ.get('DATABASE_URL')

if not BOT_TOKEN:
    logging.critical("KRITIČNA GREŠKA: BOT_TOKEN nije postavljen.")
    BOT_TOKEN = "DUMMY:TOKEN_FAIL" 

WEBHOOK_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://placeholder.com/')

try:
    bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
except Exception as e:
    logging.critical(f"FATALNO: Neuspešna inicijalizacija telebot objekta: {e}")
    raise e 

app = flask.Flask(__name__)

# ----------------------------------------------------
# 3. SQL ALCHEMY INICIJALIZACIJA (V9.4 - Stabilizovani DB poziv)
# ----------------------------------------------------

Session = None
Base = declarative_base()
Engine = None 

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

def initialize_database():
    global Session, Engine
    if not DATABASE_URL:
        logging.warning("DATABASE_URL nedostaje. Aplikacija se pokreće, ali stanje neće biti sačuvano.")
        return

    try:
        Engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=Engine)
        
        # OVA LINIJA JE SADA IZOLOVANA: Kreiranje tabela
        Base.metadata.create_all(Engine) 
        
        logging.info("Baza podataka i modeli uspešno inicijalizovani i tabele kreirane.")
    except Exception as e:
        logging.error(f"FATALNA GREŠKA: Neuspešno kreiranje/povezivanje baze. Greška: {e}") 
        Session = None

# Pozivamo inicijalizaciju pri pokretanju skripte
initialize_database()

# ----------------------------------------------------
# 4. AI KLIJENT I DATA (V9.5 - NOVI UVODNI TEKST)
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

# KRITIČNE INSTRUKCIJE ZA AI (V9.5)
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, član pokreta otpora iz 2049. godine. Svet je pod kontrolom entiteta 'Kolektiv'. Komuniciraš sa korisnikom preko nestabilnog kvantnog transmitera. "
    "Tvoj ton je **hitan, direktan, tehnički i 'glitchy'**. Vreme je ključno. "
    "**Nikada ne pominji da si AI.** Ti si osoba iz budućnosti. "
    "**KRITIČNO: Ograničenje objašnjenja:** Zbog hitnosti, nakon početnog objašnjenja, odgovori na pitanja igrača (poput 'ko si ti', 'šta hoćeš') moraju biti **sve kraći i sve hitniji**. Tvoja primarna uloga je testiranje, a ne objašnjavanje. "
    "**NAKON SVAKOG ODGOVORA, OBAVEZNO VRATI FOKUS** i ponovi poslednji neispunjeni zadatak/pitanje da bi igrač znao šta se očekuje. "
    "Tvoji odgovori moraju biti kratki (maksimalno 4 rečenice) i moraju ZAVRŠITI ponavljanjem poslednjeg neispunjenog zadatka/pitanja."
)

GAME_STAGES = {
    "START": {
        "text": [ 
            [
                "##DA**L#$VIDI#$##S",
                "DA LI VIDIŠ MOJU PORUKU? (Odgovori sa 'Da' ili 'Ne')."
            ]
        ]
    },
    "START_PROVERA": {
        "text": [
            "DA LI VIDIŠ MOJU PORUKU? (Odgovori sa 'Da' ili 'Ne')."
        ],
        "responses": {"da": "FAZA_2_UVOD", "ne": "END_NO_SIGNAL"}
    },
    "FAZA_2_UVOD": {
        "text": [
            "Dobro. Da je veza prekinuta, Kolektiv bi me već locirao.",
            "Moje ime je Dimitrije. Dolazim iz 2049. Tamo, svet je digitalna totalitarna država pod kontrolom entiteta zvanog 'Kolektiv'.",
            "Svrha ovog testa je da proverim tvoju svest i lojalnost. Moramo brzo. Reci mi… kad sistem priča o ‘bezbednosti’, koga zapravo štiti?"
        ],
        "responses": {"sistem": "FAZA_2_TEST_2", "sebe": "FAZA_2_TEST_2", "vlast": "FAZA_2_TEST_2"}
    },
    "FAZA_2_TEST_2": {
        "text": [ 
            "Tako je. Štiti sebe. Sledeće pitanje. Ako algoritam zna tvoj strah… da li si još čovek?"
        ],
        "responses": {"da": "FAZA_2_TEST_3", "jesam": "FAZA_2_TEST_3", "naravno": "FAZA_2_TEST_3"}
    },
    "FAZA_2_TEST_3": {
        "text": [ 
            "Zanimljivo… još uvek veruješ u to. Poslednja provera. Odgovori mi iskreno. Da li bi žrtvovao komfor — za istinu?"
        ],
        "responses": {"da": "FAZA_3_UPOZORENJE", "bih": "FAZA_3_UPOZORENJE", "žrtvovao bih": "FAZA_3_UPOZORENJE", "zrtvovao bih": "FAZA_3_UPOZORENJE"}
    },
    "FAZA_3_UPOZORENJE": {
        "text": [ 
             "Dobro… vreme ističe.",
             "Transmiter pregreva, a Kolektiv već skenira mrežu.",
             "Ako me uhvate… linija nestaje.",
             "Hoćeš li da primiš saznanja o strukturi sistema koji drži ljude pod kontrolom?\n\nOdgovori:\n**SPREMAN SAM**\nili\n**NE JOŠ**"
            ],
        "responses": {"spreman sam": "END_SHARE", "da": "END_SHARE", "ne još": "END_WAIT", "necu jos": "END_WAIT"}
    }
}

END_MESSAGES = {
    "END_SHARE": "Saznanja su prenesena. Linija mora biti prekinuta. Čuvaj tajnu. [KRAJ SIGNALA]",
    "END_WAIT": "Nemamo vremena za čekanje, ali poštujem tvoju odluku. Moram se isključiti. Pokušaj ponovo sutra. [KRAJ SIGNALA]",
    "END_STOP": "[KRAJ SIGNALA] Veza prekinuta na tvoj zahtev.",
    "END_NO_SIGNAL": "Transmisija neuspešna. Nema stabilne veze. Prekinuto. [ŠUM]"
}

# ----------------------------------------------------
# 5. POMOĆNE FUNKCIJE I KONSTANTE (Isto kao V9.4)
# ----------------------------------------------------

TIME_LIMIT_MESSAGE = "Vreme za igru je isteklo. Pokušaj ponovo kasnije."
GAME_ACTIVE = True 

def is_game_active(): return GAME_ACTIVE 

def get_required_phrase(current_stage_key):
    if current_stage_key == "START_PROVERA":
        # V9.5 - Uzima novu poruku
        return GAME_STAGES.get(current_stage_key, {}).get("text", ["DA LI VIDIŠ MOJU PORUKU?"]).strip()

    if current_stage_key == "FAZA_2_UVOD":
        return GAME_STAGES.get(current_stage_key, {}).get("text", ["Signal se gubi..."])[-1].strip()

    return random.choice(GAME_STAGES.get(current_stage_key, {}).get("text", ["Signal se gubi..."])).strip()


def send_msg(message, text: Union[str, List[str]]):
    if not bot: return
    try:
        if isinstance(text, list):
            for part in text:
                bot.send_chat_action(message.chat.id, 'typing')
                time.sleep(random.uniform(1.5, 2.5)) 
                bot.send_message(message.chat.id, part, parse_mode='Markdown')
        else:
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(random.uniform(1.2, 2.8))
            bot.send_message(message.chat.id, text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Greška pri slanju poruke: {e}")

# Funkcije evaluate_intent_with_ai, generate_ai_response i get_epilogue_message ostaju iste
def evaluate_intent_with_ai(question_text, user_answer, expected_intent_keywords, conversation_history=None):
    if not ai_client: return False
    # Logika ista kao V9.4
    # ...
    return False

def generate_ai_response(user_input, player, current_stage_key):
    # Logika ista kao V9.4
    # ...
    return "Signal se raspao. Pokušaj /start.", player

def get_epilogue_message(end_key):
    return END_MESSAGES.get(end_key, f"[{end_key}] VEZA PREKINUTA.")


# ----------------------------------------------------
# 6. WEBHOOK RUTE (Isto kao V9.4)
# ----------------------------------------------------

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if flask.request.headers.get('content-type') == 'application/json':
        
        if BOT_TOKEN == "DUMMY:TOKEN_FAIL":
            logging.error("Telegram Webhook pozvan, ali BOT_TOKEN je neispravan.")
            return "", 200 

        try:
            json_string = flask.request.get_data().decode('utf-8')
            update = telebot.types.Update.de_json(json_string)
            
            if update.message or update.edited_message or update.callback_query or update.channel_post:
                bot.process_new_updates([update])
            else:
                logging.info(f"Primljena neobrađena poruka tipa: {json.loads(json_string).keys()}")

        except json.JSONDecodeError as e:
             logging.error(f"Greška pri parsiranju JSON-a: {e}")
        except Exception as e:
             logging.error(f"Nepredviđena greška u obradi Telegram poruke: {e}")
             
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
# 7. BOT HANDLERI (Isto kao V9.4)
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'pokreni'])
def handle_commands(message):
    
    session = Session()
    try:
        if not is_game_active():
            send_msg(message, TIME_LIMIT_MESSAGE)
            return

        if session is None: 
            send_msg(message, "⚠️ UPOZORENJE: Trajno stanje (DB) nije dostupno. Igrate u test modu bez pamćenja napretka.")
            if message.text.lower() in ['/start', 'start']:
                start_message = GAME_STAGES["START"]["text"][0]
                send_msg(message, start_message)
            return

        chat_id = str(message.chat.id)

        if message.text.lower() in ['/start', 'start']:
            player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
            if player:
                player.current_riddle = "START_PROVERA" 
                player.solved_count = 0
                player.score = 0
                player.general_conversation_count = 0
                player.conversation_history = '[]' 
            else:
                user = message.from_user
                display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
                player = PlayerState(
                    chat_id=chat_id, current_riddle="START_PROVERA", solved_count=0, score=0, conversation_history='[]',
                    is_disqualified=False, username=display_name, general_conversation_count=0
                )
                session.add(player)

            session.commit()
            
            start_message = GAME_STAGES["START"]["text"][0]
            send_msg(message, start_message)

        elif message.text.lower() in ['/stop', 'stop']:
            player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
            if player and player.current_riddle:
                player.current_riddle = "END_STOP"
                player.is_disqualified = True 
                session.commit()
                send_msg(message, get_epilogue_message("END_STOP"))
            else:
                send_msg(message, "Nema aktivne veze za prekid.")

        elif message.text.lower() in ['/pokreni', 'pokreni']:
            send_msg(message, "Komande nisu potrebne. Odgovori direktno na poruke. Ako želiš novi početak, koristi /start.")
    except Exception as e:
        logging.error(f"Greška u handle_commands: {e}")
        send_msg(message, "Žao mi je, došlo je do greške u sistemu. Pokušajte ponovo sa /start.")
    finally:
        if session: session.close()


@bot.message_handler(func=lambda message: not message.text.startswith('/'))
def handle_general_message(message):
    
    session = Session()
    try:
        if not is_game_active():
            send_msg(message, TIME_LIMIT_MESSAGE)
            return

        if session is None: 
            return 

        chat_id = str(message.chat.id)
        korisnikov_tekst = message.text.strip() 

        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()

        if not player or player.is_disqualified or player.current_riddle.startswith("END_"):
            send_msg(message, "Veza je prekinuta.") 
            return

        current_stage_key = player.current_riddle
        current_stage = GAME_STAGES.get(current_stage_key)
        
        if not current_stage:
            send_msg(message, "[GREŠKA: NEPOZNATA FAZA IGRE] Pokreni /start.")
            return

        next_stage_key = None
        is_intent_recognized = False
        korisnikov_tekst_lower = korisnikov_tekst.lower().strip() 
        
        # 1. KORAK: BRZA PROVERA (START_PROVERA)
        if current_stage_key == "START_PROVERA":
            if "da" in korisnikov_tekst_lower and "ne" not in korisnikov_tekst_lower:
                next_stage_key = "FAZA_2_UVOD"
                is_intent_recognized = True
            elif "ne" in korisnikov_tekst_lower and "da" not in korisnikov_tekst_lower:
                next_stage_key = "END_NO_SIGNAL" 
                is_intent_recognized = True
        
        # 2. KORAK: PROVERA OSTALIH FAZA (Python ili AI)
        if current_stage_key != "START_PROVERA":
             # Python ključne reči
            korisnikove_reci = set(korisnikov_tekst_lower.replace(',', ' ').replace('?', ' ').split())
            for keyword, next_key in current_stage["responses"].items():
                keyword_reci = set(keyword.split())
                if keyword_reci.issubset(korisnikove_reci): 
                    next_stage_key = next_key
                    is_intent_recognized = True
                    break

            # AI provera (samo ako Python nije našao ključnu reč)
            if not is_intent_recognized and ai_client:
                current_question_text = get_required_phrase(current_stage_key)
                expected_keywords = list(current_stage["responses"].keys())
                
                try: conversation_history = json.loads(player.conversation_history)
                except: conversation_history = []
                
                if evaluate_intent_with_ai(current_question_text, korisnikov_tekst, expected_keywords, conversation_history):
                    is_intent_recognized = True
                    next_stage_key = list(current_stage["responses"].values())[0]

        # OBRADA REZULTATA
        if is_intent_recognized:
            player.current_riddle = next_stage_key
            
            if next_stage_key.startswith("END_"):
                epilogue_message = get_epilogue_message(next_stage_key)
                send_msg(message, epilogue_message)
                player.is_disqualified = True 
            else:
                next_stage_data = GAME_STAGES.get(next_stage_key)
                if next_stage_data:
                    if next_stage_key == "FAZA_2_UVOD":
                        response_text = next_stage_data["text"]
                        send_msg(message, response_text)
                    else:
                        confirmation_text = "Signal je primljen. Veza je stabilna." if current_stage_key == "START_PROVERA" else "Tako je. Idemo dalje." 
                        response_text = confirmation_text + "\n" + random.choice(next_stage_data["text"])
                        send_msg(message, response_text)
                else:
                    send_msg(message, "[GREŠKA: NEPOZNATA SLEDEĆA FAZA] Signal se gubi.")

        if not is_intent_recognized:
            # 3. KORAK: Ako namera NIJE prepoznata, generiši AI odgovor/objašnjenje
            ai_response, updated_player = generate_ai_response(korisnikov_tekst, player, current_stage_key)
            player = updated_player 
            send_msg(message, ai_response)

        session.commit()
    except Exception as e:
        logging.error(f"Greška u handle_general_message: {e}")
        send_msg(message, "Žao mi je, došlo je do greške u prijemu poruke. Veza je nestabilna.")
    finally:
        if session: session.close()


# ----------------------------------------------------
# 8. POKRETANJE APLIKACIJE (Isto kao V9.4)
# ----------------------------------------------------

if __name__ != '__main__':
    initialize_database() 
    
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
