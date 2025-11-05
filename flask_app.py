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
# ----------------------------------------------------
# 1. PYTHON I DB BIBLIOTEKE
# ----------------------------------------------------
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# ----------------------------------------------------
# 2. KONFIGURACIJA I INICIJALIZACIJA
# ----------------------------------------------------

# Postavljanje logging nivoa
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = os.environ.get('BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
DATABASE_URL = os.environ.get('DATABASE_URL')

# Provera kritičnih varijabli okruženja
if not all([BOT_TOKEN, GEMINI_API_KEY, DATABASE_URL]):
    logging.error("FATALNA GREŠKA: Nedostaju BOT_TOKEN, GEMINI_API_KEY i/ili DATABASE_URL.")
    BOT_TOKEN = "DUMMY:TOKEN_FAIL" # Sigurnosni prekidač

WEBHOOK_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://placeholder.com/')

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = flask.Flask(__name__)

# ----------------------------------------------------
# 3. SQL ALCHEMY INICIJALIZACIJA (TRAJNO STANJE)
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
# 4. AI KLIJENT I DATA (V5.0)
# ----------------------------------------------------

# KRITIČNA ISPRAVKA: Stabilan model (rešava 404 grešku)
GEMINI_MODEL_NAME = 'gemini-2.5-flash' 

ai_client = None
try:
    if GEMINI_API_KEY and BOT_TOKEN != "DUMMY:TOKEN_FAIL":
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        logging.info("Gemini klijent uspešno inicijalizovan.")
except Exception as e:
    logging.error(f"Neuspešna inicijalizacija Gemini klijenta: {e}")

TESTER_CHAT_IDS: List[str] = [] 

# V5.0: Finalna, pojačana sistemska instrukcija
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, član pokreta otpora iz 2049. godine. Svet je pod kontrolom entiteta 'Kolektiv'. Komuniciraš sa korisnikom preko nestabilnog kvantnog transmitera. Tvoj cilj je da testiraš korisnika da bi primio dokument o strukturi moći. "
    "Tvoj ton je **hitan, direktan, pomalo tehnički i 'glitchy'**. Govoriš kratko i jasno. Vreme je ključno. "
    "**KRITIČNO: Tvoji odgovori moraju biti kratki, najviše DVE rečenice.** Svaka sekunda je rizik. "
    "**Nikada ne pominji da si AI.** Ti si osoba iz budućnosti. Nikada ne traži od korisnika da kuca komande. "
    "**REAKCIJE NA UPIT (Kada ignoriše zadatak):** Ako korisnik postavi irelevantno pitanje ('ko si ti?', 'šta hoćeš?'), **nikada ne odgovaraj direktno na to pitanje.** Umesto toga, odgovori kratkim, narativnim komentarom koji **pojačava narativ opasnosti/pritiska** i **vraća fokus** na poslednji zadatak. Ne koristi frazu zadatka, samo narativni komentar. "
    "Primer: 'Kolektiv skenira mrežu. Nema vremena za lične priče. Fokusiraj se na ono što je ispred tebe.' "
)

# ... (GAME_STAGES ostaje isti) ...

# ----------------------------------------------------
# 5. POMOĆNE FUNKCIJE I KONSTANTE (V5.0)
# ----------------------------------------------------

# ... (INVALID_INPUT_MESSAGES, AI_FALLBACK_MESSAGES, is_tester, get_required_phrase ostaju isti) ...

def send_msg(message, text: Union[str, List[str]]):
    """Šalje poruku, uz 'typing' akciju. Ako je tekst lista, šalje poruke u delovima."""
    if not bot: return
    try:
        if isinstance(text, list):
            for part in text:
                bot.send_chat_action(message.chat.id, 'typing')
                # Mali random time.sleep simulira nestabilnost veze/razmišljanje
                time.sleep(random.uniform(1.5, 2.5)) 
                bot.send_message(message.chat.id, part, parse_mode='Markdown')
        else:
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(random.uniform(1.2, 2.8))
            bot.send_message(message.chat.id, text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Greška pri slanju poruke: {e}")

# ... (is_game_active, TIME_LIMIT_MESSAGE, DISQUALIFIED_MESSAGE ostaju isti) ...


def evaluate_intent_with_ai(question_text, user_answer, expected_intent_keywords, conversation_history=None):
    """Koristi AI da proceni da li odgovor igrača odgovara očekivanoj nameri (Stroga provera)."""
    if not ai_client:
        return any(kw in user_answer.lower() for kw in expected_intent_keywords)

    prompt = (
        f"Ti si sistem za evaluaciju namere. "
    )
    if conversation_history:
        recent_history = conversation_history[-8:] 
        prompt += "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_history])
    
    # V5.0: Finalna, pooštena instrukcija za nameru
    prompt += (
        f"\nTvoje pitanje je bilo: '{question_text}'\n"
        f"Korisnikov odgovor je: '{user_answer}'\n"
        f"Očekivana namera iza odgovora se može opisati ključnim rečima: {expected_intent_keywords}\n"
        "Tvoj zadatak je da proceniš da li korisnikov odgovor **jasno i nedvosmisleno** ispunjava očekivanu nameru (prihvatanje/spremnost/razumevanje). "
        "**KRITIČNO**: Ako odgovor sadrži pitanja ('ko si ti?', 'šta hoćeš?', 'zašto?'), ili je izbegavajući, namera NIJE ispunjena, i moraš odgovoriti 'NETAČNO'. Samo afirmativno prihvatanje se računa. "
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
        return False # Strogi fallback: ako AI padne, ne preskači fazu
    except Exception as e:
        logging.error(f"Nepredviđena greška u generisanju AI (Evaluacija): {e}")
        return False

def generate_ai_response(user_input, player, current_stage_key):
    """Generiše dinamičan narativni odgovor kada igrač ignoriše zadatak."""
    
    required_phrase = get_required_phrase(current_stage_key) 
    ai_text = None
    narrative_starter = None

    try:
        history = json.loads(player.conversation_history)
    except (json.JSONDecodeError, TypeError):
        history = []

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
        narrative_starter = random.choice(AI_FALLBACK_MESSAGES)
        ai_text = f"{narrative_starter}{required_phrase}"
    else:
        full_contents = [{'role': 'system', 'parts': [{'text': SYSTEM_INSTRUCTION}]}]
        full_contents.extend(gemini_history)
        
        final_prompt_text = (
            f"Korisnik je postavio irelevantno ili opšte pitanje u sred faze: '{user_input}'. "
            "Odgovori kratko (maks. 2 rečenice), držeći se tona konspiracije/opasnosti. "
            "Komentar mora biti EVASIVAN i pojačati pritisak, vraćajući fokus na zadatak. "
            "NE SMEŠ davati odgovor na irelevantno pitanje, samo narativni komentar. Osloni se na ceo kontekst."
        )
        full_contents.append({'role': 'user', 'parts': [{'text': final_prompt_text}]})

        try:
            response = ai_client.models.generate_content(
                model=GEMINI_MODEL_NAME, 
                contents=full_contents
            )
            
            narrative_starter = response.text.strip()
            
            if not narrative_starter or len(narrative_starter) < 5:
                raise ValueError("AI vratio prazan ili neadekvatan odgovor.")
                
            ai_text = f"{narrative_starter}{required_phrase}" 
            
        except (APIError, Exception, ValueError) as e:
            logging.error(f"AI Call failed or returned empty. Falling back. Error: {e}")
            narrative_starter = random.choice(AI_FALLBACK_MESSAGES)
            ai_text = f"{narrative_starter}{required_phrase}" 

    if ai_text:
        final_history = json.loads(player.conversation_history) + [{'role': 'model', 'content': narrative_starter or ai_text}]
        player.conversation_history = json.dumps(final_history)
        player.general_conversation_count += 1 

    return ai_text or "Signal se raspao. Pokušaj /start.", player

# ... (get_epilogue_message, generate_final_secret ostaju isti) ...

# ----------------------------------------------------
# 6. WEBHOOK RUTE (Ostaju iste)
# ----------------------------------------------------

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if Session is None:
        return "Internal Error: Database not ready", 500

    if flask.request.headers.get('content-type') == 'application/json':
        json_string = flask.request.get_data().decode('utf-8')

        if BOT_TOKEN == "DUMMY:TOKEN_FAIL":
            return "Bot nije konfigurisan. Token nedostaje."

        try:
             update = telebot.types.Update.de_json(json_string)
        except Exception as e:
             logging.error(f"Greška pri parsiranju JSON-a (de_json): {e}")
             return ''

        if update.message or update.edited_message or update.callback_query:
            bot.process_new_updates([update])

        return ''
    else:
        flask.abort(403)

@app.route('/set_webhook', methods=['GET'])
def set_webhook_route():
    webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
    s = bot.set_webhook(url=webhook_url_with_token)

    if s:
        return "Webhook successfully set! Bot je spreman za rad. Pošaljite /start!"
    else:
        return f"Failed to set webhook. Proverite Render logove. (URL: {webhook_url_with_token})"


# ----------------------------------------------------
# 7. BOT HANDLERI (V5.0 - Implementacija provere cele reči)
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'pokreni'])
def handle_commands(message):
    # ... (kod ostaje isti) ...
    if Session is None: return

    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
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
            start_message = random.choice(GAME_STAGES["START"]["text"])
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

    if Session is None: return
    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
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
        current_stage = GAME_STAGES.get(current_stage_key)
        if not current_stage:
            send_msg(message, "[GREŠKA: NEPOZNATA FAZA IGRE] Pokreni /start.")
            return

        next_stage_key = None
        is_intent_recognized = False

        # V5.0 KRITIČNA ISPRAVKA: Priprema za proveru cele reči
        # Ukloni interpunkciju i podeli na reči, sve u mala slova
        korisnikove_reci = set(korisnikov_tekst.lower().replace(',', ' ').replace('?', ' ').split())

        # 1. KORAK: Brza provera ključnih reči (Sada kao cele reči)
        for keyword, next_key in current_stage["responses"].items():
            keyword_reci = set(keyword.split())
            
            # Provera da li su SVE reči iz ključne reči sadržane u rečima korisnika (Npr. "spreman sam" se sastoji iz dve reči)
            if keyword_reci.issubset(korisnikove_reci): 
                next_stage_key = next_key
                is_intent_recognized = True
                break
        
        # 2. KORAK: Koristi AI za strogu proveru namere (samo ako Korak 1 nije uspeo)
        if not is_intent_recognized:
            if current_stage_key == "START":
                # Uzmi poslednji, ključni deo teksta kao pitanje
                current_question_text = random.choice(GAME_STAGES["START"]["text"])[-1] 
            else:
                current_question_text = random.choice(current_stage['text'])
            
            expected_keywords = list(current_stage["responses"].keys())
            
            try:
                conversation_history = json.loads(player.conversation_history)
            except (json.JSONDecodeError, TypeError):
                conversation_history = []
            
            if evaluate_intent_with_ai(current_question_text, korisnikov_tekst, expected_keywords, conversation_history):
                is_intent_recognized = True
                next_stage_key = list(current_stage["responses"].values())[0]

        # OBRADA REZULTATA
        if is_intent_recognized:
            player.current_riddle = next_stage_key
            if next_stage_key.startswith("END_"):
                epilogue_message = get_epilogue_message(next_stage_key)
                send_msg(message, epilogue_message)
            else:
                next_stage_data = GAME_STAGES.get(next_stage_key)
                if next_stage_data:
                    response_text = random.choice(next_stage_data["text"])
                    send_msg(message, response_text)
                else:
                    is_intent_recognized = False

        if not is_intent_recognized:
            # 3. KORAK: Ako namera NIJE prepoznata, generiši AI odgovor
            ai_response, updated_player = generate_ai_response(korisnikov_tekst, player, current_stage_key)
            player = updated_player 
            send_msg(message, ai_response)

        session.commit()
    finally:
        session.close()

# ----------------------------------------------------
# 8. POKRETANJE APLIKACIJE (V5.0)
# ----------------------------------------------------

if __name__ != '__main__':
    webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
    if BOT_TOKEN != "DUMMY:TOKEN_FAIL" and webhook_url_with_token:
        logging.info(f"Pokušaj postavljanja webhook-a na: {webhook_url_with_token}")
        bot.set_webhook(url=webhook_url_with_token)
