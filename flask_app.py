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
# 3. SQL ALCHEMY INICIJALIZACIJA
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
# 4. AI KLIJENT I DATA (V9.0)
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

# NOVE KRITIČNE INSTRUKCIJE ZA AI (V9.0)
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
                "## DA&^&&LI#M#ME##",
                "[GLITCH / ERROR]",
                "Da li vidis moju poruku? (Odgovori sa 'Da' ili 'Ne')."
            ]
        ]
        # START ne sadrži responses jer se obrađuje u handle_commands
    },
    "START_PROVERA": {
        "text": [
            "Da li vidis moju poruku? (Odgovori sa 'Da' ili 'Ne')."
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
    # Faze 2_TEST_1, 2_TEST_2, 2_TEST_3 iz stare logike su sada pomerene za jedan korak, 
    # ali radi jednostavnosti, ostavljamo ih kao sledeće faze, ali sa novim tekstom:
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
# Dodajemo novu END fazu
END_MESSAGES = {
    "END_SHARE": "Saznanja su prenesena. Linija mora biti prekinuta. Čuvaj tajnu. [KRAJ SIGNALA]",
    "END_WAIT": "Nemamo vremena za čekanje, ali poštujem tvoju odluku. Moram se isključiti. Pokušaj ponovo sutra. [KRAJ SIGNALA]",
    "END_STOP": "[KRAJ SIGNALA] Veza prekinuta na tvoj zahtev.",
    "END_NO_SIGNAL": "Transmisija neuspešna. Nema stabilne veze. Prekinuto. [ŠUM]" # Nova poruka za neuspeh
}

# ----------------------------------------------------
# 5. POMOĆNE FUNKCIJE I KONSTANTE (V9.0)
# ----------------------------------------------------

TIME_LIMIT_MESSAGE = "Vreme za igru je isteklo. Pokušaj ponovo kasnije."
GAME_ACTIVE = True 

def is_game_active():
    return GAME_ACTIVE 

def get_required_phrase(current_stage_key):
    # Za fazu START_PROVERA, traži se poslednja rečenica
    if current_stage_key == "START_PROVERA":
        return GAME_STAGES.get(current_stage_key, {}).get("text", ["Signal se gubi..."])[0].strip()

    # Za fazu FAZA_2_UVOD, traži se poslednja rečenica (test pitanje)
    if current_stage_key == "FAZA_2_UVOD":
        return GAME_STAGES.get(current_stage_key, {}).get("text", ["Signal se gubi..."])[-1].strip()

    # Za ostale faze, koristi jedinu rečenicu u "text"
    return random.choice(GAME_STAGES.get(current_stage_key, {}).get("text", ["Signal se gubi..."])).strip()

def send_msg(message, text: Union[str, List[str]]):
    # ... (funkcija ostaje ista, koristi END_MESSAGES)
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

# Funkcije evaluate_intent_with_ai i generate_ai_response ostaju neizmenjene (uz novi SYSTEM_INSTRUCTION)
def evaluate_intent_with_ai(question_text, user_answer, expected_intent_keywords, conversation_history=None):
    # ... (logika iz V8.0)
    if not ai_client: return False

    prompt = (
        f"Ti si sistem za evaluaciju namere. "
    )
    if conversation_history:
        recent_history = conversation_history[-8:] 
        prompt += "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_history])
    
    prompt += (
        f"\nTvoje pitanje je bilo: '{question_text}'\n"
        f"Korisnikov odgovor je: '{user_answer}'\n"
        f"Očekivana namera je JASNO AFIRMATIVAN odgovor ili odgovor koji sadrži ključnu reč: {expected_intent_keywords}. "
        "**KRITIČNO**: Odgovori koji sadrže upitne reči (ko, šta, gde, zašto, kako) ili su izbegavajući, **NISU** ispunjenje namere. "
        "Ako korisnik postavi pitanje (npr. 'zašto?'), to NIJE ispunjenje namere, osim ako je ključna reč u pitanju. "
        "Odgovori samo sa jednom rečju: 'TAČNO' ako je namera ispunjena, ili 'NETAČNO' ako nije."
    )
    
    try:
        response = ai_client.models.generate_content(
            model=GEMINI_MODEL_NAME, 
            contents=[prompt],
            config={"temperature": 0.0}
        )
        return "TAČNO" in response.text.upper()
    except APIError as e: 
        logging.error(f"Greška AI/Gemini API (Evaluacija namere): {e}")
        return False
    except Exception as e:
        logging.error(f"Nepredviđena greška u generisanju AI (Evaluacija): {e}")
        return False

def generate_ai_response(user_input, player, current_stage_key):
    # ... (logika iz V8.0)
    required_phrase = get_required_phrase(current_stage_key) 
    ai_text = None
    
    try: history = json.loads(player.conversation_history)
    except: history = []

    MAX_HISTORY_ITEMS = 10
    if len(history) > MAX_HISTORY_ITEMS:
        history = history[-MAX_HISTORY_ITEMS:]

    gemini_history = []
    for entry in history:
        role = 'user' if entry['role'] == 'user' else 'model' 
        gemini_history.append({'role': role, 'parts': [{'text': entry['content']}]})
    
    updated_history = history + [{'role': 'user', 'content': user_input}]
    player.conversation_history = json.dumps(updated_history)

    if not ai_client:
        AI_FALLBACK_MESSAGES = ["Veza je nestabilna. Ponavljaj poruku.", "Čujem samo šum… ponovi!"]
        narrative_starter = random.choice(AI_FALLBACK_MESSAGES)
        ai_text = f"{narrative_starter}\n\n{required_phrase}"
    else:
        full_contents = [{'role': 'system', 'parts': [{'text': SYSTEM_INSTRUCTION}]}]
        full_contents.extend(gemini_history)
        
        final_prompt_text = (
            f"Korisnik je postavio kontekstualno pitanje/komentar: '{user_input}'. "
            f"Tvoj poslednji zadatak je bio: '{required_phrase}'. "
            "Generiši kratak odgovor (maks. 4 rečenice), dajući traženo objašnjenje i/ili pojačavajući pritisak, a zatim OBAVEZNO ponovi poslednji zadatak/pitanje."
        )
        full_contents.append({'role': 'user', 'parts': [{'text': final_prompt_text}]})

        try:
            response = ai_client.models.generate_content(
                model=GEMINI_MODEL_NAME, 
                contents=full_contents
            )
            narrative_starter = response.text.strip()
            
            if not narrative_starter or len(narrative_starter) < 5: 
                 raise ValueError("AI vratio prazan odgovor.")
                
            ai_text = narrative_starter
            
        except Exception as e:
            logging.error(f"AI Call failed. Falling back. Error: {e}")
            AI_FALLBACK_MESSAGES = ["Veza je nestabilna. Ponavljaj poruku.", "Čujem samo šum… ponovi!"]
            narrative_starter = random.choice(AI_FALLBACK_MESSAGES)
            ai_text = f"{narrative_starter}\n\n{required_phrase}" 

    if ai_text:
        final_history = json.loads(player.conversation_history) + [{'role': 'model', 'content': narrative_starter or ai_text}]
        player.conversation_history = json.dumps(final_history)
        player.general_conversation_count += 1 

    return ai_text or "Signal se raspao. Pokušaj /start.", player

def get_epilogue_message(end_key):
    return END_MESSAGES.get(end_key, f"[{end_key}] VEZA PREKINUTA.")
    
# ----------------------------------------------------
# 6. WEBHOOK RUTE (ostaju neizmenjene)
# ----------------------------------------------------

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    # ...
    return ''

@app.route('/set_webhook', methods=['GET'])
def set_webhook_route():
    # ...
    return f"Webhook successfully set to: {webhook_url_with_token}! Bot je spreman. Pošaljite /start!"

# ----------------------------------------------------
# 7. BOT HANDLERI (V9.0 - Nova START logika)
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'pokreni'])
def handle_commands(message):
    
    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
        return

    if Session is None: 
        send_msg(message, "GREŠKA: Trajno stanje (DB) nije dostupno. Igru možete započeti, ali napredak neće biti sačuvan.")
        if message.text.lower() in ['/start', 'start']:
            start_message = GAME_STAGES["START"]["text"][0]
            send_msg(message, start_message)
        return

    chat_id = str(message.chat.id)
    session = Session()

    try:
        if message.text.lower() in ['/start', 'start']:
            player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
            if player:
                # KRITIČNO: Postavlja fazu na START_PROVERA
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
            
            # Šalje novu uvodnu poruku sa šumom
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

    finally:
        session.close()


@bot.message_handler(func=lambda message: not message.text.startswith('/'))
def handle_general_message(message):
    
    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
        return

    if Session is None: 
        return

    chat_id = str(message.chat.id)
    korisnikov_tekst = message.text.strip() 
    session = Session()

    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()

        if not player or player.is_disqualified or player.current_riddle.startswith("END_"):
            # Ne daje uputstvo za /start
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
        
        # 1. KORAK: BRZA PROVERA (START_PROVERA i ostale faze)
        if current_stage_key == "START_PROVERA":
            if "da" in korisnikov_tekst_lower and "ne" not in korisnikov_tekst_lower:
                next_stage_key = "FAZA_2_UVOD"
                is_intent_recognized = True
            elif "ne" in korisnikov_tekst_lower and "da" not in korisnikov_tekst_lower:
                next_stage_key = "END_NO_SIGNAL" # Kritični prekid!
                is_intent_recognized = True
        
        elif current_stage_key == "FAZA_2_UVOD" or current_stage_key.startswith("FAZA_2_TEST_"):
             # Provera za ostale faze (Python ključne reči)
            korisnikove_reci = set(korisnikov_tekst_lower.replace(',', ' ').replace('?', ' ').split())
            for keyword, next_key in current_stage["responses"].items():
                keyword_reci = set(keyword.split())
                if keyword_reci.issubset(korisnikove_reci): 
                    next_stage_key = next_key
                    is_intent_recognized = True
                    break

        # 2. KORAK: KORISTI AI ZA STROGU PROVERU (Ako nije u START fazi i nije prošlo Korak 1)
        if not is_intent_recognized and current_stage_key != "START_PROVERA" and ai_client:
            
            current_question_text = get_required_phrase(current_stage_key)
            expected_keywords = list(current_stage["responses"].keys())
            
            try: conversation_history = json.loads(player.conversation_history)
            except: conversation_history = []
            
            if evaluate_intent_with_ai(current_question_text, korisnikov_tekst, expected_keywords, conversation_history):
                is_intent_recognized = True
                # Uzimamo prvu vrednost jer je AI potvrdio nameru
                next_stage_key = list(current_stage["responses"].values())[0]

        # OBRADA REZULTATA
        if is_intent_recognized:
            player.current_riddle = next_stage_key
            
            if next_stage_key.startswith("END_"):
                epilogue_message = get_epilogue_message(next_stage_key)
                send_msg(message, epilogue_message)
                player.is_disqualified = True # Diskvalifikuj ako je kraj
            else:
                next_stage_data = GAME_STAGES.get(next_stage_key)
                if next_stage_data:
                    # Za FAZA_2_UVOD šaljemo ceo narativni tekst
                    if next_stage_key == "FAZA_2_UVOD":
                        response_text = next_stage_data["text"]
                        send_msg(message, response_text)
                    else:
                        # Za ostale faze, šaljemo potvrdnu rečenicu i novo pitanje
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
    finally:
        session.close()

# ----------------------------------------------------
# 8. POKRETANJE APLIKACIJE
# ----------------------------------------------------

if __name__ != '__main__':
    # ... (kod za inicijalizaciju Webhooka)
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
