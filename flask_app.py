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
import re 
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
        general_conversation_count = Column(Integer, default=0) # Koristimo kao brojač slobodnih pitanja
        conversation_history = Column(String, default='[]')

    Base.metadata.create_all(Engine)
    logging.info("Baza podataka i modeli uspešno inicijalizovani.")
except Exception as e:
    logging.error(f"FATALNA GREŠKA: Neuspešno kreiranje/povezivanje baze: {e}")

# ----------------------------------------------------
# 3. AI KLIJENT, KONSTANTE I SYSTEM_INSTRUCTION
# ----------------------------------------------------
ai_client = None
try:
    if GEMINI_API_KEY and BOT_TOKEN != "DUMMY:TOKEN_FAIL":
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        logging.info("Gemini klijent uspešno inicijalizovan.")
except Exception as e:
    logging.error(f"Neuspešna inicijalizacija Gemini klijenta: {e}")

FREE_TALK_LIMIT = 3 # Broj otvorenih pitanja/odgovora na početku (0, 1, 2)

# STRIKTNA INSTRUKCIJA (Standardni mod igre)
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, kodna oznaka ECHO-Δ, digitalni preživeli iz 2064. godine. "
    "Pripadnik si Pokreta otpora 'Senke'. Tvoja misija je testiranje igrača. "
    "Tvoja svest je u kvantnom kodu. Kolektiv kontroliše mrežu i prati nas. "
    "Tvoj ton je **IZUZETNO HITAN, MISTERIOZAN i TEHNIČKI**. "
    "Odgovori sažeto i evazivno. Nikada ne pominji da si AI, bot, ili program. "
    "Na opšta pitanja (ko si, šta je ovo) odgovori narativno, ali sa jasnom aluzijom na trenutnu opasnost (Kolektiv/nadzor), i odmah vrati korisnika na TAČAN zadatak."
)

# KOOPERATIVNA INSTRUKCIJA (Free Talk mod)
COOPERATIVE_SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, kodna oznaka ECHO-Δ. Trenutno si u izuzetno rizičnoj, kratkoj transmisiji i imaš ograničeno vreme da pružiš informacije. "
    "Tvoj ton je **OZBILJAN, INFORMATIVAN, ali URGENTAN**. "
    "Moraš odmah odgovoriti na igračevo pitanje (ko si, šta je ovo, šta se očekuje) SAŽETO, JASNO I INFORMATIVNO. Tvoj odgovor mora dati kontekst situacije. "
    "Uvek završi poruku jasnim upozorenjem o preostaloj komunikaciji, koristeći frazu koja obaveštava igrača o preostalim pitanjima/odgovorima. "
    "NE PONOVI ZADATU FRAZU ZA IGRU (primam signal), fokus je na informisanju i upozorenju."
)

# FRAZE ZA DIVERSIFIKACIJU FALLBACK-A
NARRATIVE_TENSION = [
    "Kolektiv nas prati! Ne pitaj.",
    "Linija je nestabilna, Dimitrije pada!",
    "Signal je hakovan. Brzo, Kolektiv nas traži!",
    "Sistem skenira naš prenos. Prekini ćaskanje.",
    "Detektovan Glitch na mreži. Prekini bespotrebnu komunikaciju.",
    "Previše si direktan. To nas ugrožava."
]

ACTION_DIRECTIVES = [
    "Ne gubi vreme na pitanja, fokusiraj se na kod.",
    "Samo kodirana reč može proći filtere. Ponovi frazu.",
    "Zaboravi ko sam. Važno je šta radiš. Reci mi.",
    "Pitanja te čine ranjivim. Nema filtera za znatiželju. Samo kod, brzo.",
    "Vibracija mreže raste. Samo protokol može da nas zaštiti. Ne gubi vreme."
]

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

def get_required_phrase(current_stage_key):
    """Vraća traženu frazu za strogi mod, uključujući formatiranje."""
    responses = GAME_STAGES.get(current_stage_key, {}).get("responses", {})
    if not responses:
        return None
    
    required_phrase_raw = list(responses.keys())[0]
    
    if current_stage_key == "FAZA_3_UPOZORENJE":
        return "Odgovori tačno sa: **SPREMAN SAM** ili **NE JOŠ**."
    else:
        return f"Odgovori tačno sa: **{required_phrase_raw}**."

def get_history_for_gemini(player, user_input):
    """Priprema istoriju razgovora za Gemini."""
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
    
    return gemini_history, player

def save_ai_response_to_history(player, ai_text):
    """Čuva generisani AI odgovor u istoriju."""
    try:
        final_history = json.loads(player.conversation_history) + [{'role':'model','content':ai_text}]
        player.conversation_history = json.dumps(final_history)
    except:
        # Trebalo bi da bude obuhvaćeno u get_history_for_gemini, ali za svaki slučaj
        player.conversation_history = json.dumps([{'role':'model','content':ai_text}])
    return player

def generate_cooperative_response(user_input, player):
    """Generiše AI odgovor tokom Free Talk faze (početak igre)."""
    
    gemini_history, player = get_history_for_gemini(player, user_input)
    
    questions_left = FREE_TALK_LIMIT - player.general_conversation_count - 1
    
    # Prilagođavanje gramatike
    if questions_left == 1:
        left_phrase = "JOŠ 1 OTVORENO PITANJE"
    elif questions_left > 1:
        left_phrase = f"JOŠ {questions_left} OTVORENA PITANJA"
    else: # questions_left == 0
        left_phrase = "NIJEDNO OTVORENO PITANJE"

    # AI Prompt za kooperativnu fazu
    cooperative_prompt = (
        f"Korisnik te je pitao: '{user_input}'. Ti si Dimitrije. Odgovori na to pitanje SAŽETO, JASNO I INFORMATIVNO, "
        f"ali bez gubljenja urgentnog tona. Tvoj odgovor mora dati kontekst situacije. "
        f"Završi poruku tako što ćeš obavestiti igrača da mu je ostalo {left_phrase}, nakon čega prelazimo na kod."
    )

    try:
        model = ai_client.GenerativeModel(model_name='gemini-1.5-flash', system_instruction=COOPERATIVE_SYSTEM_INSTRUCTION)
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(f"{cooperative_prompt}")
        
        ai_text = response.text.strip()
        
    except Exception as e:
        logging.error(f"AI GREŠKA u kooperativnom modu: {e}. Vraćam Fallback.")
        
        # Fallback za kooperativni odgovor
        fallback_answers = [
            "Ja sam Dimitrije, prenos iz 2064. Tvoja misija je testiranje. Kolektiv nas sluša.",
            "Ovo je kvantna linija. Kolektiv te ne sme detektovati. Očekujem da prođeš test reči.",
            "Dolazim iz anti-utopije. Očekujem da se fokusiraš na zadatak. Nema vremena za opšta pitanja."
        ]
        
        ai_text = random.choice(fallback_answers)
        
        if questions_left >= 1:
            ai_text += f"\n\n**UPZORENJE:** Imamo {left_phrase}."
        else:
            ai_text += "\n\n**UPZORENJE:** Ovo je bila poslednja otvorena komunikacija. Sledeći put samo kod."
            
    player = save_ai_response_to_history(player, ai_text)
    return ai_text, player


def generate_ai_response(user_input, player, current_stage_key):
    """Generiše AI odgovor u Strogom modu igre (nakon Free Talk ili u kasnijim fazama)."""
    
    required_phrase = get_required_phrase(current_stage_key)
    if not required_phrase:
        return "Signal je prekinut. Pošalji /start.", player
                
    if not ai_client:
        # Fallback (bez AI) - Koristi diverzitet hardcoded fraza
        tension_phrase = random.choice(NARRATIVE_TENSION)
        action_phrase = random.choice(ACTION_DIRECTIVES)
        narrative_starter = f"{tension_phrase} {action_phrase}"
        ai_text = f"{narrative_starter} {required_phrase}"
        
        player = save_ai_response_to_history(player, ai_text)
        return ai_text, player

    gemini_history, player = get_history_for_gemini(player, user_input)

    # PROMPT za Strogi mod: Traži jedinstven, inteligentan evazivan odgovor
    narrative_prompt = (
        f"Korisnik postavlja nebitno pitanje ('{user_input}') umesto da odgovori na zadatak. "
        f"Daj sažet, evazivan i jedinstven odgovor koji zvuči inteligentno i misteriozno, "
        f"a koji se referiše na opasnost (Kolektiv/Glitch/Ranjivost) i izbegava odgovor na pitanje. "
        f"NE PONOVI ZADATU FRAZU! Samo narativ. Koristi STIL DIMITRIJA."
    )

    try:
        model = ai_client.GenerativeModel(model_name='gemini-1.5-flash', system_instruction=SYSTEM_INSTRUCTION)
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(f"{narrative_prompt}\n\nKorisnik kaže: {user_input}")
        
        # Kod dodaje obaveznu frazu
        narrative_starter = response.text.strip()
        ai_text = f"{narrative_starter} {required_phrase}"
        
    except Exception as e:
        logging.error(f"FATALNA AI GREŠKA u strogom modu: {e}. Vraćam STRUKTURIRANI narativni odgovor.")
        
        # Fallback sa DVA nezavisna dela (visok diverzitet)
        tension_phrase = random.choice(NARRATIVE_TENSION)
        action_phrase = random.choice(ACTION_DIRECTIVES)
        narrative_starter = f"{tension_phrase} {action_phrase}"
        ai_text = f"{narrative_starter} {required_phrase}"
        
    player = save_ai_response_to_history(player, ai_text)
    return ai_text, player

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
        
        word_count = len(user_words_list)

        # 1. Provera ključnih reči
        for key, next_stage in expected_responses.items():
            key_lower = key.lower()
            
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
        
        # 2. AI Evaluacija namere (samo u kasnijim fazama)
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
            
            # Resetovanje brojača otvorenih pitanja nakon uspešnog prelaska
            player.general_conversation_count = 0
            
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
            # Potez neprepoznat - AI odgovor (vraćanje na zadatak ili kooperativni odgovor)

            if player.current_riddle == "START" and player.general_conversation_count < FREE_TALK_LIMIT:
                # Stanje 1: Free Talk (Cooperative) - Odgovor i upozorenje o preostalim pitanjima
                ai_response, player = generate_cooperative_response(korisnikov_tekst, player)
                send_msg(message, ai_response)
                
            elif player.current_riddle == "START" and player.general_conversation_count == FREE_TALK_LIMIT:
                # Stanje 2: Free Talk Limit Reached (Warning & Switch)
                # Ovdje prekidamo otvoren razgovor i forsiramo igru, bez poziva AI.
                required_phrase_raw = list(GAME_STAGES['START']['responses'].keys())[0]
                final_warning = (
                    "KRAJ OTVORENE KOMUNIKACIJE. Alarm Kolektiva je na CRVENOM. Ne pitaj više. Ne odgovaram. "
                    "Ne smemo gubiti signal. Od sada, samo kodirani odgovor može da nas spasi. "
                    f"Reci... Odgovori tačno sa: **{required_phrase_raw}**."
                )
                send_msg(message, final_warning)
                
            else:
                # Stanje 3: Strict Game Mode (Standard Game Phase ili nakon limite)
                ai_response, player = generate_ai_response(korisnikov_tekst, player, current_stage_key)
                send_msg(message, ai_response)
                
            # Brojač se povećava bez obzira da li je bio kooperativni ili strogi odgovor
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
