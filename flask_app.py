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
from telebot.apihelper import ApiTelegramException 

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
# 4. AI KLIJENT I DATA
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
# 5. POMOĆNE FUNKCIJE I KONSTANTE (V5.2.2 - KRITIČNO FIKSIRANJE LOGIKE)
# ----------------------------------------------------

TIME_LIMIT_MESSAGE = "Vreme za igru je isteklo. Pokušaj ponovo kasnije."
GAME_ACTIVE = True 

def is_game_active():
    """Provera da li je igra trenutno aktivna. Trenutno uvek TAČNO."""
    return GAME_ACTIVE 

def get_required_phrase(current_stage_key):
    responses = GAME_STAGES.get(current_stage_key, {}).get("responses", {})
    if not responses: return ""
    required_phrase_raw = list(responses.keys())[0]
    if current_stage_key == "FAZA_3_UPOZORENJE":
        return "\n\nOdgovori tačno sa:\n**SPREMAN SAM**\nili\n**NE JOŠ**"
    elif current_stage_key == "START":
        return f"\n\nAko si i dalje tu, reci: **{required_phrase_raw}**."
    else:
        return f"\n\nVreme ističe. Samo kodirana reč: **{required_phrase_raw}**."

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

def evaluate_intent_with_ai(question_text, user_answer, expected_intent_keywords, conversation_history=None):
    """Koristi AI da proceni da li odgovor igrača odgovara očekivanoj nameri (Ekstremno Stroga provera)."""
    if not ai_client: return False

    prompt = (
        f"Ti si sistem za evaluaciju namere. "
    )
    if conversation_history:
        recent_history = conversation_history[-8:] 
        prompt += "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_history])
    
    # KRITIČNO POOŠTRAVANJE INSTRUKCIJE ZA TAČNU EVALUACIJU
    prompt += (
        f"\nTvoje pitanje je bilo: '{question_text}'\n"
        f"Korisnikov odgovor je: '{user_answer}'\n"
        f"Očekivana namera iza odgovora je prihvatanje ili potvrda spremnosti. "
        "**KRITIČNO**: Odgovori koji sadrže upitne reči (ko, šta, gde, zašto, kako) ili su izbegavajući, **NISU** ispunjenje namere. "
        "Takođe, odgovor ne sme sadržati kritične reči koje su bile definisane u tekstu pitanja, kao što su: 'primam signal' "
        "ako odgovor NIJE doslovno 'primam signal'. Samo jasan afirmativan odgovor se računa. "
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
    required_phrase = get_required_phrase(current_stage_key) 
    ai_text = None
    narrative_starter = None

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
            
            if not narrative_starter or len(narrative_starter) < 5: raise ValueError("AI vratio prazan odgovor.")
                
            ai_text = f"{narrative_starter}{required_phrase}" 
            
        except Exception as e:
            logging.error(f"AI Call failed. Falling back. Error: {e}")
            AI_FALLBACK_MESSAGES = ["Veza je nestabilna. Ponavljaj poruku.", "Čujem samo šum… ponovi!"]
            narrative_starter = random.choice(AI_FALLBACK_MESSAGES)
            ai_text = f"{narrative_starter}{required_phrase}" 

    if ai_text:
        final_history = json.loads(player.conversation_history) + [{'role': 'model', 'content': narrative_starter or ai_text}]
        player.conversation_history = json.dumps(final_history)
        player.general_conversation_count += 1 

    return ai_text or "Signal se raspao. Pokušaj /start.", player

def get_epilogue_message(end_key):
    if end_key == "END_SHARE":
        return "Saznanja su prenesena. Linija mora biti prekinuta. Čuvaj tajnu. [KRAJ SIGNALA]"
    elif end_key == "END_WAIT":
        return "Nemamo vremena za čekanje, ali poštujem tvoju odluku. Moram se isključiti. Pokušaj ponovo sutra. [KRAJ SIGNALA]"
    elif end_key == "END_STOP":
        return "[KRAJ SIGNALA] Veza prekinuta na tvoj zahtev."
    return f"[{end_key}] VEZA PREKINUTA."

def generate_final_secret(chat_id):
    # Logika za generisanje tajne - može se kasnije proširiti
    import hashlib
    # Koristimo heširanje da bismo dobili unikatnu, ali predvidivu tajnu za svakog korisnika
    hash_object = hashlib.sha256(f"SECRET_OF_KOLEKTIV_{chat_id}_{time.time()}".encode())
    return hash_object.hexdigest()[:12].upper()


# ----------------------------------------------------
# 6. WEBHOOK RUTE 
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
# 7. BOT HANDLERI 
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'pokreni'])
def handle_commands(message):
    
    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
        return

    if Session is None: 
        send_msg(message, "GREŠKA: Trajno stanje (DB) nije dostupno. Igru možete započeti, ali napredak neće biti sačuvan.")
        if message.text.lower() in ['/start', 'start']:
            start_message = random.choice(random.choice(GAME_STAGES["START"]["text"]))
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
        current_stage = GAME_STAGES.get(current_stage_key)
        if not current_stage:
            send_msg(message, "[GREŠKA: NEPOZNATA FAZA IGRE] Pokreni /start.")
            return

        next_stage_key = None
        is_intent_recognized = False

        # 1. KORAK: Brza provera ključnih reči (celi podniz mora biti pronađen)
        korisnikove_reci = set(korisnikov_tekst.lower().replace(',', ' ').replace('?', ' ').split())

        for keyword, next_key in current_stage["responses"].items():
            keyword_reci = set(keyword.split())
            
            # Provera da li su sve reči ključne reči prisutne u korisnikovom tekstu
            if keyword_reci.issubset(korisnikove_reci): 
                next_stage_key = next_key
                is_intent_recognized = True
                break
        
        # 2. KORAK: Koristi AI za strogu proveru namere
        if not is_intent_recognized:
            if current_stage_key == "START":
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
