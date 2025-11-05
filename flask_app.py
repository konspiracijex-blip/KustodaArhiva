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
import re # Dodat import za regex
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, time as dt_time

# ----------------------------------------------------
# 1. PYTHON I DB BIBLIOTEKE
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
# 2. SQL ALCHEMY INICIJALIZACIJA (TRAJNO STANJE)
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

# AŽURIRANA INSTRUKCIJA ZA STROGI NARATIV
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, kodna oznaka ECHO-Δ, digitalni preživeli iz 2064. godine. "
    "Pripadnik si Pokreta otpora 'Senke'. Tvoja misija je testiranje igrača. "
    "Tvoja svest je u kvantnom kodu. Kolektiv kontroliše mrežu i prati nas. "
    "Tvoj ton je **IZUZETNO HITAN, GLITCHY, MISTERIOZAN i TEHNIČKI**. "
    "Odgovori **maksimalno JEDNA, DVE KRATKE REČENICE**. Nikada ne pominji da si AI, bot, ili program. "
    "Na opšta pitanja (ko si, šta je ovo) odgovori narativno, **EVASIVNO**, i uvek vraćaj korisnika na TAČAN zadatak. "
    "Uvek završi odgovor traženom frazom za prelazak faze igre."
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
    required_phrase = "Nema zadatka. Molim te pošalji /start."
    if current_stage_key in GAME_STAGES:
        responses = GAME_STAGES[current_stage_key].get("responses", {})
        if responses:
            key = next(iter(responses.keys()))
            if current_stage_key == "FAZA_3_UPOZORENJE":
                required_phrase = "Odgovori tačno sa: **SPREMAN SAM** ili **NE JOŠ**."
            else:
                required_phrase = f"Odgovori tačno sa: **{key}**."
                
    if not ai_client:
        return f"[GREŠKA: NEMA KVANATNOG PRISTUPA. LINIJA NESTABILNA.] Moramo dalje. Brzo, reci: {required_phrase}", player

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

    task_reminder_prompt = (
        f"Ignoriši korisnikovo pitanje. Ti si Dimitrije. Daj kratak, hitan i evazivan odgovor. "
        f"Odmah usmeri korisnika na zadatak, koristeći traženu frazu: '{required_phrase}'. "
    )

    try:
        model = ai_client.GenerativeModel(model_name='gemini-1.5-flash', system_instruction=SYSTEM_INSTRUCTION)
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(f"{task_reminder_prompt}\n\nKorisnik kaže: {user_input}")
        ai_text = response.text
        
        final_history = json.loads(player.conversation_history) + [{'role':'model','content':ai_text}]
        player.conversation_history = json.dumps(final_history)
        return ai_text, player
    except Exception as e:
        logging.error(f"FATALNA AI GREŠKA: {e}. Vraćam STRUKTURIRANI narativni odgovor.")
        
        narrative_fallback = random.choice([
            "Kolektiv nas prati! Ne pitaj. Skreni im pažnju. Samo mi reci...",
            "Nema vremena za filozofiranje, linija je nestabilna. Moramo dalje. Brzo...",
            "Previše si direktan. To nas ugrožava. Samo me to može zadržati u mreži. Reci...",
            "Glitch na mreži. Prekini bespotrebnu komunikaciju. Moram da čujem kodiran odgovor...",
            "Skeniraju prenos. Samo kodirana reč može proći filtere. Ponovi frazu...",
            "Zaboravi ko sam. Važno je šta radiš. Ne možemo gubiti signal. Reci mi..."
        ])
        
        ai_text = f"{narrative_fallback} {required_phrase}"
        
        final_history = json.loads(player.conversation_history) + [{'role':'model','content':ai_text}]
        player.conversation_history = json.dumps(final_history)
        
        return ai_text, player

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
    if Session is None: return

    session = Session()
    chat_id = str(message.chat.id)
    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        if player:
            player.current_riddle = "START"
            player.solved_count = 0
            player.score = 0
            player.general_conversation_count = 0
            player.conversation_history = '[]'
            player.is_disqualified = False
        else:
            user = message.from_user
            display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
            player = PlayerState(
                chat_id=chat_id, 
                current_riddle='START', 
                username=display_name
            )
            session.add(player)
            
        session.commit()
        
        stage_text = GAME_STAGES['START']['text'][0]
        send_msg(message, stage_text)
    except Exception as e:
        logging.error(f"Greška u handle_start: {e}")
    finally:
        session.close()

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    if Session is None: return

    session = Session()
    chat_id = str(message.chat.id)
    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        if not player or player.current_riddle.startswith("END") or player.is_disqualified:
            send_msg(message, "Veza je prekinuta. Pošalji /start za uspostavljanje nove veze.")
            return

        current_stage_key = player.current_riddle
        stage_data = GAME_STAGES.get(current_stage_key, {})
        expected_responses = stage_data.get("responses", {})
        
        korisnikov_tekst = message.text.strip().lower()
        
        user_words_list = re.split(r'\W+', korisnikov_tekst)
        user_words = set(filter(None, user_words_list))
        
        matched_stage = None
        is_intent_recognized = False
        
        # Broj reči u korisnikovom unosu (za proveru kratkih reči)
        word_count = len(user_words_list)

        # 1. Provera ključnih reči
        for key, next_stage in expected_responses.items():
            key_lower = key.lower()
            
            # POPRAVLJENA LOGIKA ZA VIŠESTRUKE I JEDNOSTUREKE KLJUČNE REČI
            if ' ' in key_lower:
                # Ključna reč je fraza (npr. "primam signal", "spreman sam"). Provera potpunog podudaranja.
                if key_lower == korisnikov_tekst:
                    matched_stage = next_stage
                    is_intent_recognized = True
                    break
            else:
                # Ključna reč je jedna reč (npr. "sistem", "da"). Provera skupa reči.
                if key_lower in user_words:
                    # Stroga provera za kratke reči ('da', 'bih') unutar dugog konteksta.
                    if key_lower in ['da', 'bih'] and word_count > 3:
                        continue
                        
                    matched_stage = next_stage
                    is_intent_recognized = True
                    break
        
        # 2. AI Evaluacija namere
        if not is_intent_recognized and current_stage_key not in ["START"]:
            current_question_text = random.choice(stage_data.get('text', ["..."])).replace('\n', ' ')
            expected_keywords = list(expected_responses.keys())
            
            try:
                conversation_history = json.loads(player.conversation_history)
            except:
                conversation_history = []
                
            if evaluate_intent_with_ai(current_question_text, korisnikov_tekst, expected_keywords, conversation_history):
                is_intent_recognized = True
                matched_stage = list(expected_responses.values())[0]

        if matched_stage:
            # Potez prepoznat - prelazak na sledeću fazu
            player.current_riddle = matched_stage
            if matched_stage.startswith("END"):
                send_msg(message, get_epilogue_message(matched_stage))
            else:
                next_stage_data = GAME_STAGES[matched_stage]
                
                text_options = next_stage_data['text']
                if isinstance(text_options[0], list):
                    next_text = random.choice(text_options)[-1] 
                elif isinstance(text_options[0], str):
                    next_text = random.choice(text_options)
                else:
                     next_text = "Signal je prekinut."
                
                send_msg(message, next_text)
        else:
            # Potez neprepoznat - AI odgovor (vraćanje na zadatak)
            ai_response, player = generate_ai_response(korisnikov_tekst, player, current_stage_key)
            send_msg(message, ai_response)
            player.general_conversation_count += 1
            
        session.commit()
    except Exception as e:
        logging.error(f"Greška u handle_message: {e}")
        send_msg(message, "Signal je nestabilan. Nešto nije u redu sa mrežom.")
    finally:
        session.close()

# ----------------------------------------------------
# 7. FLASK WEBHOOK I RUTIRANJE
# ----------------------------------------------------
@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if Session is None:
        logging.error("Database Session nije uspostavljen. Bot ne može obrađivati poruke.")
        return "Internal Error: Database not ready", 500
        
    if flask.request.headers.get('content-type') == 'application/json':
        try:
            json_str = flask.request.get_data().decode('utf-8')
            update = telebot.types.Update.de_json(json_str)
            bot.process_new_updates([update])
            return '', 200
        except Exception as e:
            logging.error(f"Webhook greška: {e}")
            return '', 500
    else:
        flask.abort(403)

@app.route('/')
def index():
    return "Dimitrije Bot je aktivan i čeka na signal. Webhook putanja: /" + BOT_TOKEN

# ----------------------------------------------------
# 8. POKRETANJE (WEBHOOK SETUP)
# ----------------------------------------------------
@app.route('/set_webhook_manual', methods=['GET'])
def set_webhook_route():
    webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
    s = bot.set_webhook(url=webhook_url_with_token)

    if s:
        return f"Webhook uspešno postavljen! URL: {webhook_url_with_token}"
    else:
        return "Neuspelo postavljanje webhooka."

if __name__ == "__main__":
    if BOT_TOKEN != "DUMMY:TOKEN_FAIL":
        webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
        logging.info(f"Pokušaj postavljanja webhook-a na: {webhook_url_with_token}")
        bot.set_webhook(url=webhook_url_with_token)
    
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
