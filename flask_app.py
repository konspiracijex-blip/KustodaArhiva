import flask
import telebot
import os
import logging
import random
import time
import json
import re 
from google import genai
from google.genai.errors import APIError
from typing import List, Union
# ----------------------------------------------------
# 1. PYTHON I DB BIBLIOTEKE
# ----------------------------------------------------
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
# 4. AI KLIJENT I DATA (V6.0.1 - NOVE INSTRUKCIJE)
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

# NOVE INSTRUKCIJE ZA GEMINI AI (V6.0.1 - Više objašnjenja/konteksta)
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, član pokreta otpora iz 2049. godine. Svet je pod kontrolom entiteta 'Kolektiv'. Tvoj ton je **hitan, direktan, tehnički i 'glitchy'**. "
    "Tvoj glavni cilj je da testiraš korisnika kroz 4 faze i preneseš mu saznanja o strukturi sistema. "
    "**AKO KORISNIK POSTAVI KONTEKSTUALNO PITANJE (npr. 'ko si ti?', 'o čemu se radi?'):** Odgovori ukratko (maks. 3 rečenice) dajući mu **nužne informacije** o sebi (Dimitrije) i pretnji (Kolektiv, 2049.) kako bi mu objasnio kontekst. "
    "**NAKON SVAKOG ODGOVORA, OBAVEZNO VRATI FOKUS** na test pitanje trenutne faze, ili postavi novo test pitanje ako je prethodno zadovoljeno. "
    "**KRITIČNA PRAVILA TRANZICIJE FAZA (Koristi samo kada je odgovor igrača JASNO POTVRDAN i afirmativan):** "
    "1. Ako je korisnik odgovorio afirmativno na poslednji test faze 'START', završi svoj odgovor kodom: **[NEXT_FAZA_2_TEST_1]** "
    "2. Ako je korisnik odgovorio afirmativno na poslednji test faze 'FAZA_2_TEST_1', završi svoj odgovor kodom: **[NEXT_FAZA_2_TEST_2]** "
    "3. Ako je korisnik odgovorio afirmativno na poslednji test faze 'FAZA_2_TEST_2', završi svoj odgovor kodom: **[NEXT_FAZA_2_TEST_3]** "
    "4. Ako je korisnik odgovorio afirmativno na poslednji test faze 'FAZA_2_TEST_3', završi svoj odgovor kodom: **[NEXT_FAZA_3_UPOZORENJE]** "
    "5. Ako je korisnik odgovorio 'SPREMAN SAM' na fazu 'FAZA_3_UPOZORENJE', završi odgovor kodom: **[NEXT_END_SHARE]** "
    "6. Ako je korisnik odgovorio 'NE JOŠ' na fazu 'FAZA_3_UPOZORENJE', završi odgovor kodom: **[NEXT_END_WAIT]** "
    "**NIKADA NE KORISTI OVE KODOVE osim kada je USLOV FAZE ISPUNJEN.** Uključi te kodove na kraju, odvojene od teksta. "
)

GAME_STAGES = {
    "START": {
        "text": ["Ako si spreman, odgovori: **primam signal**."],
        "full_text": [
             "Hej… ako ovo čuješ, znači da smo spojeni.",
             "Moje ime nije važno, ali možeš me zvati Dimitrije.",
             "Dolazim iz budućnosti u kojoj Orwellove reči nisu fikcija.",
             "Sve što si mislio da je fikcija… postalo je stvarnost.",
             "Ako si spreman, odgovori: **primam signal**."
        ]
    },
    "FAZA_2_TEST_1": {
        "text": ["Reci mi… kad sistem priča o ‘bezbednosti’, koga zapravo štiti?"],
        "full_text": ["Dobro. Prvi filter je prošao. Reci mi… kad sistem priča o ‘bezbednosti’, koga zapravo štiti?"]
    },
    "FAZA_2_TEST_2": {
        "text": ["Ako algoritam zna tvoj strah… da li si još čovek?"],
        "full_text": ["Tako je. Štiti sebe. Sledeće pitanje. Ako algoritam zna tvoj strah… da li si još čovek?"]
    },
    "FAZA_2_TEST_3": {
        "text": ["Odgovori mi iskreno. Da li bi žrtvovao komfor — za istinu?"],
        "full_text": ["Zanimljivo… još uvek veruješ u to. Poslednja provera. Odgovori mi iskreno. Da li bi žrtvovao komfor — za istinu?"]
    },
    "FAZA_3_UPOZORENJE": {
        "text": ["Hoćeš li da primiš saznanja o strukturi sistema koji drži ljude pod kontrolom?\n\nOdgovori:\n**SPREMAN SAM**\nili\n**NE JOŠ**"],
        "full_text": [
             "Dobro… vreme ističe.",
             "Transmiter pregreva, a Kolektiv već skenira mrežu.",
             "Ako me uhvate… linija nestaje.",
             "Postoji piramida moći. Hoćeš li da primiš saznanja o strukturi sistema koji drži ljude pod kontrolom?\n\nOdgovori:\n**SPREMAN SAM**\nili\n**NE JOŠ**"
        ]
    }
}
# ----------------------------------------------------
# 5. POMOĆNE FUNKCIJE I KONSTANTE
# ----------------------------------------------------

TIME_LIMIT_MESSAGE = "Vreme za igru je isteklo. Pokušaj ponovo kasnije."
GAME_ACTIVE = True 

def is_game_active():
    return GAME_ACTIVE 

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

# NOVA FUNKCIJA ZA FULL AI KONTROLU
def generate_ai_response_v6(user_input, player, current_stage_key):
    # Mapiranje kodova za tranziciju (mora se slagati sa SYSTEM_INSTRUCTION)
    NEXT_STAGE_MAPPING = {
        "[NEXT_FAZA_2_TEST_1]": "FAZA_2_TEST_1",
        "[NEXT_FAZA_2_TEST_2]": "FAZA_2_TEST_2",
        "[NEXT_FAZA_2_TEST_3]": "FAZA_2_TEST_3",
        "[NEXT_FAZA_3_UPOZORENJE]": "FAZA_3_UPOZORENJE",
        "[NEXT_END_SHARE]": "END_SHARE",
        "[NEXT_END_WAIT]": "END_WAIT"
    }
    
    ai_text = None
    next_stage_key = None
    narrative_text = None

    try: history = json.loads(player.conversation_history)
    except: history = []

    MAX_HISTORY_ITEMS = 10
    if len(history) > MAX_HISTORY_ITEMS:
        history = history[-MAX_HISTORY_ITEMS:]

    gemini_history = []
    for entry in history:
        role = 'user' if entry['role'] == 'user' else 'model' 
        gemini_history.append({'role': role, 'parts': [{'text': entry['content']}]})
    
    # 1. Priprema istorije
    updated_history = history + [{'role': 'user', 'content': user_input}]
    player.conversation_history = json.dumps(updated_history)
    
    # 2. Određivanje trenutnog testa za AI prompt
    current_test_question = random.choice(GAME_STAGES.get(current_stage_key, {}).get('text', ["Gde si stao?"]))

    if not ai_client:
        narrative_text = "Veza je nestabilna. Ponavljaj poruku."
        return narrative_text, None, player
    
    # 3. Kreiranje finalnog prompta
    full_contents = [{'role': 'system', 'parts': [{'text': SYSTEM_INSTRUCTION}]}]
    full_contents.extend(gemini_history)
    
    final_prompt_text = (
        f"Trenutna faza testiranja je: '{current_stage_key}'. Trenutno test pitanje je: '{current_test_question}'. "
        f"Korisnikov odgovor: '{user_input}'. "
        "Generiši kratak odgovor, odgovarajući na korisnikovo pitanje/komentar. Ako je odgovor ujedno i afirmativna potvrda testa, uključi odgovarajući [NEXT_FAZA_X] kod na samom kraju poruke. "
        "Ako uslov testa nije ispunjen, NE UKLJUČUJ NIKAKAV [NEXT_...] KOD, već samo ponovi test pitanje."
    )
    full_contents.append({'role': 'user', 'parts': [{'text': final_prompt_text}]})

    try:
        response = ai_client.models.generate_content(
            model=GEMINI_MODEL_NAME, 
            contents=full_contents
        )
        ai_text = response.text.strip()
        
        if not ai_text or len(ai_text) < 5: raise ValueError("AI vratio prazan odgovor.")
        
        # 4. Analiza odgovora na tajni kod
        for code, stage in NEXT_STAGE_MAPPING.items():
            if code in ai_text:
                next_stage_key = stage
                narrative_text = ai_text.replace(code, '').strip() # Uklanjamo kod iz poruke
                break
        
        if not next_stage_key:
             narrative_text = ai_text # Ako nema koda, ceo tekst je narativni odgovor

    except Exception as e:
        logging.error(f"AI Call failed. Falling back. Error: {e}")
        narrative_text = "Signal se raspao. Pokušaj /start." 
        next_stage_key = None

    # 5. Ažuriranje istorije bota (samo generisani tekst, bez koda)
    if narrative_text:
        final_history = json.loads(player.conversation_history) + [{'role': 'model', 'content': narrative_text}]
        player.conversation_history = json.dumps(final_history)
        player.general_conversation_count += 1 

    return narrative_text, next_stage_key, player

# Funkcije epiloga ostaju neizmenjene
def get_epilogue_message(end_key):
    if end_key == "END_SHARE":
        return "Saznanja su prenesena. Linija mora biti prekinuta. Čuvaj tajnu. [KRAJ SIGNALA]"
    elif end_key == "END_WAIT":
        return "Nemamo vremena za čekanje, ali poštujem tvoju odluku. Moram se isključiti. Pokušaj ponovo sutra. [KRAJ SIGNALA]"
    elif end_key == "END_STOP":
        return "[KRAJ SIGNALA] Veza prekinuta na tvoj zahtev."
    return f"[{end_key}] VEZA PREKINUTA."

def generate_final_secret(chat_id):
    import hashlib
    hash_object = hashlib.sha256(f"SECRET_OF_KOLEKTIV_{chat_id}_{time.time()}".encode())
    return hash_object.hexdigest()[:12].upper()


# ----------------------------------------------------
# 6. WEBHOOK RUTE 
# ----------------------------------------------------
# ... (Webhook rute ostaju neizmenjene) ...
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
# 7. BOT HANDLERI (V6.0.1)
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'pokreni'])
def handle_commands(message):
    
    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
        return

    if Session is None: 
        send_msg(message, "GREŠKA: Trajno stanje (DB) nije dostupno. Igru možete započeti, ali napredak neće biti sačuvan.")
        if message.text.lower() in ['/start', 'start']:
            start_message = GAME_STAGES["START"]["full_text"]
            send_msg(message, start_message)
        return

    chat_id = str(message.chat.id)
    session = Session()

    try:
        if message.text.lower() in ['/start', 'start']:
            player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
            if player:
                player.current_riddle = "START" 
                player.solved_count = 0
                player.score = 0
                player.general_conversation_count = 0
                player.conversation_history = '[]' 
            else:
                user = message.from_user
                display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
                player = PlayerState(
                    chat_id=chat_id, current_riddle="START", solved_count=0, score=0, conversation_history='[]',
                    is_disqualified=False, username=display_name, general_conversation_count=0
                )
                session.add(player)

            session.commit()
            start_message = GAME_STAGES["START"]["full_text"]
            send_msg(message, start_message)

        elif message.text.lower() in ['/stop', 'stop']:
            player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
            if player and player.current_riddle:
                player.current_riddle = "END_STOP"
                player.is_disqualified = True 
                session.commit()
                send_msg(message, "[KRAJ SIGNALA] Veza prekinuta na tvoj zahtev.")
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
            send_msg(message, "Veza je prekinuta. Pošalji /start za uspostavljanje nove veze.")
            return

        current_stage_key = player.current_riddle
        
        # U V6.0.1, KORISTIMO SAMO FULL AI KONTROLU
        ai_response, next_stage_key, updated_player = generate_ai_response_v6(korisnikov_tekst, player, current_stage_key)
        player = updated_player 

        if next_stage_key:
            # AI je odlučio da pređe na sledeću fazu!
            player.current_riddle = next_stage_key
            
            # Ako je to bio kod za kraj igre, šaljemo epilog
            if next_stage_key.startswith("END_"):
                send_msg(message, ai_response) # Šaljemo AI odgovor koji je bio okidač
                epilogue_message = get_epilogue_message(next_stage_key)
                send_msg(message, epilogue_message)
            else:
                # Prešli smo na novu fazu, šaljemo AI odgovor koji sadrži novo pitanje
                send_msg(message, ai_response)
        else:
            # AI je generisao samo narativni odgovor (nije ispunjen uslov za prelazak)
            send_msg(message, ai_response)

        session.commit()
    finally:
        session.close()

# ----------------------------------------------------
# 8. POKRETANJE APLIKACIJE
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
