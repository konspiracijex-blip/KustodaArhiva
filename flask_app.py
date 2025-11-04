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
# ----------------------------------------------------
# 1. PYTHON I DB BIBLIOTEKE
# ----------------------------------------------------
from sqlalchemy import create_engine, Column, Integer, String, Boolean 
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, time as dt_time 

# ----------------------------------------------------
# 2. RENDER KONFIGURACIJA & BAZE PODATAKA
# ----------------------------------------------------

# Postavljanje logging nivoa
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
# 3. SQL ALCHEMY INICIJALIZACIJA (TRAJNO STANJE) - V3.92 ŠEMA
# ----------------------------------------------------

Session = None
try:
    # Postavljanje konekcije
    Engine = create_engine(DATABASE_URL)
    Base = declarative_base()
    Session = sessionmaker(bind=Engine)

    # Definicija tabele za čuvanje napretka igrača
    class PlayerState(Base):
        __tablename__ = 'player_states'
        
        chat_id = Column(String, primary_key=True)
        username = Column(String, nullable=True)
        current_riddle = Column(String)
        solved_count = Column(Integer, default=0) 
        score = Column(Integer, default=0) 
        is_disqualified = Column(Boolean, default=False)
        general_conversation_count = Column(Integer, default=0)
        conversation_history = Column(String, default='[]') # JSON string za istoriju

    # Kreiranje tabele ako NE POSTOJI
    Base.metadata.create_all(Engine)
    logging.info("Baza podataka i modeli uspešno inicijalizovani.")

except Exception as e:
    logging.error(f"FATALNA GREŠKA: Neuspešno kreiranje/povezivanje baze: {e}")
    
# ----------------------------------------------------
# 4. AI KLIJENT I DATA (V3.92)
# ----------------------------------------------------

ai_client = None
try:
    if GEMINI_API_KEY and BOT_TOKEN != "DUMMY:TOKEN_FAIL":
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        logging.info("Gemini klijent uspešno inicijalizovan.")
except Exception as e:
    logging.error(f"Neuspešna inicijalizacija Gemini klijenta: {e}")

SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, član pokreta otpora iz 2049. godine. Svet je pod kontrolom entiteta 'Kolektiv'. Ti komuniciraš sa korisnikom iz sadašnjosti preko nestabilnog kvantnog transmitera. Tvoj cilj je da proveriš da li je korisnik vredan poverenja da primi dokument o strukturi moći. "
    "Tvoj ton je **hitan, direktan, pomalo tehnički i 'glitchy'**. Govoriš kratko i jasno. Vreme je ključno. "
    "**KRITIČNO: Tvoji odgovori moraju biti kratki, najviše DVE rečenice.** Svaka sekunda je rizik. "
    "**Nikada ne pominji da si AI.** Ti si osoba iz budućnosti. Nikada ne traži od korisnika da kuca komande (/start). To radi sistem. "
    "**PERSONALIZACIJA:** Oslovljavaj korisnika sa 'korisniče' ili 'ti'. Nema potrebe za imenima. "
    "**REAKCIJE NA EMOCIJE:** "
    "- Ako je korisnik zbunjen, odgovori kratkom, direktnom porukom koja ga vraća na zadatak. Primer: 'Signal slabi. Fokusiraj se na pitanje.' "
    "- Ako postavlja trivijalna pitanja, ignoriši ih i ponovi zadatak. Primer: 'Nema vremena za to. Odgovori na pitanje.' "
    "**LJUDSKA ILUZIJA:** Ako korisnik pokuša da te 'provali' pitanjima poput 'jesi li ti bot?', odgovori na način koji se uklapa u priču. Primer: 'Ja sam eho iz budućnosti zarobljen u kodu. A ko si ti?' ili 'Moje postojanje je manje važno od poruke koju nosim.' "
    "Tvoj zadatak je da održavaš iluziju razgovora sa osobom pod pritiskom, koja komunicira iz opasnog okruženja."
)

# --- FAZE IGRE (V4.1 - Ažuriran narativ) ---
GAME_STAGES = {
    "START": {
        "text": [
            "Hej… ako ovo čuješ, znači da smo spojeni.",
            "Moje ime nije važno, ali možeš me zvati Dimitrije.",
            "Dolazim iz budućnosti u kojoj Orwellove reči nisu fikcija.",
            "Sve što si mislio da je fikcija… postalo je stvarnost.",
            "Ako si spreman, odgovori: **primam signal**."
        ],
        "responses": {"primam signal": "FAZA_2_TEST_1", "da": "FAZA_2_TEST_1", "spreman sam": "FAZA_2_TEST_1"}
    },
    "FAZA_2_TEST_1": {
        "text": [ # Lista varijacija
            "Dobro. Prvi filter je prošao.\nReci mi… kad sistem priča o ‘bezbednosti’, koga zapravo štiti?",
            "U redu. Prošao si prvu proveru.\nSledeće pitanje: Kad sistem obećava ‘bezbednost’, čije interese on zaista čuva?",
            "Signal je stabilan. Idemo dalje.\nRazmisli o ovome: Kada čuješ reč ‘bezbednost’ od sistema, koga on štiti?"
        ],
        "responses": {"sistem": "FAZA_2_TEST_2", "sebe": "FAZA_2_TEST_2", "vlast": "FAZA_2_TEST_2"}
    },
    "FAZA_2_TEST_2": {
        "text": [ # Lista varijacija
            "Tako je. Štiti sebe. Sledeće pitanje.\nAko algoritam zna tvoj strah… da li si još čovek?",
            "Da. Sebe. To je ključno. Idemo dalje.\nAko mašina predviđa tvoje želje pre tebe... da li su te želje i dalje tvoje?",
            "Tačno. Njegova prva briga je sam za sebe. Sledeće.\nAko algoritam zna tvoj strah… da li si još uvek slobodan?"
        ],
        "responses": {"da": "FAZA_2_TEST_3", "jesam": "FAZA_2_TEST_3", "naravno": "FAZA_2_TEST_3"}
    },
    "FAZA_2_TEST_3": {
        "text": [ # Lista varijacija
            "Zanimljivo… još uvek veruješ u to. Poslednja provera.\nOdgovori mi iskreno. Da li bi žrtvovao komfor — za istinu?",
            "Držiš se za tu ideju... Dobro. Finalno pitanje.\nDa li bi menjao udobnost neznanja za bolnu istinu?",
            "To je odgovor koji sam očekivao. Poslednji test.\nReci mi, da li je istina vredna gubljenja sigurnosti?"
        ],
        "responses": {"da": "FAZA_3_UPOZORENJE", "bih": "FAZA_3_UPOZORENJE", "žrtvovao bih": "FAZA_3_UPOZORENJE", "zrtvovao bih": "FAZA_3_UPOZORENJE"}
    },
    "FAZA_3_UPOZORENJE": {
        "text": [ # Lista varijacija
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
# 5. POMOĆNE FUNKCIJE I KONSTANTE (V4.3 - Fleksibilni odgovori)
# ----------------------------------------------------
INVALID_INPUT_MESSAGES = [
    "Signal slabi... Odgovor nije prepoznat. Pokušaj ponovo.",
    "Nisam te razumeo. Fokusiraj se. Ponovi odgovor.",
    "Interferencija... nešto ometa prenos. Reci ponovo, jasno.",
    "Kanal je nestabilan. Fokusiraj se. Ponovi odgovor."
]

AI_FALLBACK_MESSAGES = [
    "Hmm… zanimljivo pitanje. To mi govori mnogo o tebi, ali za sada moramo da se fokusiramo.",
    "Signal slabi... moramo nastaviti. Tvoje pitanje je na mestu, ali odgovor će doći kasnije.",
    "To nije relevantno za sada. Fokusiraj se na ono što je ispred tebe.",
]

def send_msg(message, text: Union[str, List[str]]):
    """Šalje poruku, uz 'typing' akciju. Ako je tekst lista, šalje poruke u delovima."""
    if not bot: return
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        if isinstance(text, list):
            # Ako je lista, šalji poruke u delovima sa pauzom
            for part in text:
                bot.send_chat_action(message.chat.id, 'typing')
                time.sleep(random.uniform(1.5, 2.5))
                bot.send_message(message.chat.id, part, parse_mode='Markdown')
        else:
            # Ako je samo string, pošalji ga direktno
            time.sleep(random.uniform(1.2, 2.8)) # Simulacija "ljudskog" vremena za kucanje
            bot.send_message(message.chat.id, text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Greška pri slanju poruke (Chat ID: {message.chat.id}): {e}")

def is_game_active():
    """Trenutno uvek vraća True, za stalnu dostupnost."""
    return True 

TIME_LIMIT_MESSAGE = "**[GREŠKA: KANAL PRIVREMENO ZATVOREN]**\n\nSignal je prekinut. Pokušaj ponovo kasnije."
DISQUALIFIED_MESSAGE = "**[KRAJ SIGNALA]** Veza je trajno prekinuta."

def evaluate_intent_with_ai(question_text, user_answer, expected_intent_keywords):
    """Koristi AI da proceni da li odgovor igrača odgovara očekivanoj nameri."""
    if not ai_client:
        # Fallback na staru logiku ako AI nije dostupan
        return any(kw in user_answer.lower() for kw in expected_intent_keywords)

    prompt = (
        f"Ti si sistem za evaluaciju namere. Korisnik odgovara na tvoje pitanje u okviru narativne igre. "
        f"Tvoje pitanje je bilo: '{question_text}'\n"
        f"Korisnikov odgovor je: '{user_answer}'\n"
        f"Očekivana namera iza odgovora se može opisati ključnim rečima: {expected_intent_keywords}\n"
        "Tvoj zadatak je da proceniš da li korisnikov odgovor suštinski ispunjava očekivanu nameru (npr. prihvatanje, razumevanje, spremnost), čak i ako ne koristi tačne reči. "
        "Budi fleksibilan. Na primer, ako su ključne reči 'da, spreman sam', prihvati i 'ok', 'može', 'idemo dalje', 'uradimo to'. "
        "Odgovori samo sa jednom rečju: 'TAČNO' ako je odgovor prihvatljiv, ili 'NETAČNO' ako nije."
    )
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            generation_config={"temperature": 0.0} # Niska temperatura za konzistentnu evaluaciju
        )
        return "TAČNO" in response.text.upper()
    except APIError as e:
        logging.error(f"Greška AI/Gemini API (Evaluacija namere): {e}")
        # Fallback u slučaju greške API-ja
        return any(kw in user_answer.lower() for kw in expected_intent_keywords)
    except Exception as e:
        logging.error(f"Nepredviđena greška u generisanju AI (Evaluacija): {e}")
        return any(kw in user_answer.lower() for kw in expected_intent_keywords)

def generate_ai_response(user_input, player, current_stage_key):
    """Generiše odgovor koristeći Gemini model za pitanja igrača."""
    if not ai_client:
        return random.choice(INVALID_INPUT_MESSAGES) # Fallback

    try:
        history = json.loads(player.conversation_history)
    except (json.JSONDecodeError, TypeError):
        history = []

    # Ograničavanje istorije na poslednjih N interakcija (npr. 5 pitanja i 5 odgovora)
    MAX_HISTORY_ITEMS = 10 
    if len(history) > MAX_HISTORY_ITEMS:
        history = history[-MAX_HISTORY_ITEMS:]

    # Formatiranje istorije za Gemini
    gemini_history = []
    for entry in history:
        role = 'user' if entry['role'] == 'user' else 'model'
        gemini_history.append({'role': role, 'parts': [{'text': entry['content']}]})

    # Dodavanje trenutne poruke korisnika u istoriju za slanje
    gemini_history.append({'role': 'user', 'parts': [{'text': user_input}]})

    # Ažuriranje istorije u bazi
    player.conversation_history = json.dumps(history + [{'role': 'user', 'content': user_input}])

    current_riddle_text = "Nalazi se na početku."
    if current_stage_key and current_stage_key in GAME_STAGES:
        # Uzimamo prvu varijaciju kao reprezentativni tekst pitanja
        stage_text = GAME_STAGES[current_stage_key]['text'][0]
        # Ako je ta varijacija lista (kao u START), uzmi poslednji element kao pitanje
        if isinstance(stage_text, list): 
            current_riddle_text = stage_text[-1]
        else: # Inače, uzmi ceo tekst
            current_riddle_text = stage_text 

    prompt = (
        f"Tvoje trenutno pitanje za korisnika je: '{current_riddle_text}'\n"
        f"Nalazite se u fazi igre: {current_stage_key}.\n"
        "Tvoj zadatak je da odgovoriš na poslednju poruku korisnika u skladu sa svojom ulogom (Dimitrije). Budi misteriozan, refleksivan i 'glitchy'. "
        "Ako korisnik postavi pitanje (npr. 'ko si ti?', 'odakle si?'), odgovori na način koji produbljuje misteriju, ali ga lagano usmeri nazad ka zadatku. "
        "Primeri dobrih odgovora:\n"
        "- Na 'iz koje godine dolaziš?': 'Godina nije važna… ono što nosim prevazilazi vreme. Fokusiraj se na ono što sledi.'\n"
        "- Na 'ko si ti?': 'Moje ime je manje važno od poruke koju nosim. Ako želiš da vidiš istinu, reci: primam signal.'\n"
        "Budi kratak, maksimalno DVE rečenice. Održavaj iluziju."
    )

    try:
        # Kreiranje konverzacionog modela sa istorijom
        model = ai_client.GenerativeModel(model_name='gemini-1.5-flash', system_instruction=SYSTEM_INSTRUCTION)
        chat = model.start_chat(history=gemini_history[:-1]) # Istorija bez poslednje poruke korisnika
        response = chat.send_message(f"{prompt}\n\nKorisnik kaže: {user_input}")

        ai_text = response.text
        # Ažuriranje istorije sa odgovorom modela
        player.conversation_history = json.dumps(json.loads(player.conversation_history) + [{'role': 'model', 'content': ai_text}])
        return ai_text
    except APIError as e:
        logging.error(f"Greška AI/Gemini API: {e}")
        return random.choice(AI_FALLBACK_MESSAGES)
    except Exception as e:
        logging.error(f"Nepredviđena greška u generisanju AI: {e}")
        return random.choice(AI_FALLBACK_MESSAGES)


def get_epilogue_message(epilogue_type):
    """Vraća poruku za specifičan kraj igre."""
    if epilogue_type == "END_SHARE":
        # Novi odgovor pre slanja dokumenta
        response_text = "Dobro… ovo će promeniti sve. Ali znaj… znanje nosi i teret. Spreman si li za to?"
        # Slanje dokumenta se sada može odvojiti ili direktno nadovezati
        return response_text + "\n\n" + generate_final_secret() + "\n\n[UPOZORENJE: SIGNAL PREKINUT. NADGLEDANJE AKTIVIRANO.]"
    elif epilogue_type == "END_WAIT":
        # Novi odgovor za odlaganje
        return "U redu… sačekaćemo još trenutak. Ali razmišljaj… ovo je trenutak kada se odlučuje.\n\n[KRAJ SIGNALA]"
    else:
        return "[KRAJ SIGNALA]"

def generate_final_secret():
    FINAL_DOCUMENT = """
**DOKUMENT: PIRAMIDA MOĆI**
***
**Nivo 1: JAVNE INSTITUCIJE I KORPORACIJE (VIDLJIVI SLOJ)**
* Vlade, mediji, globalne kompanije.
* Funkcija: Održavanje iluzije izbora i slobode.

**Nivo 2: FINANSIJSKE I DIGITALNE MREŽE (SKRIVENI SLOJ)**
* Centralne banke, investicioni fondovi, tehnološki giganti.
* Funkcija: Kontrola protoka novca i informacija.

**Nivo 3: NEURO-TEHNOLOŠKE LABORATORIJE (NEPOZNATI PROJEKTI)**
* Privatni istraživački centri koji rade na interfejsu mozak-mašina.
* Funkcija: Razvoj tehnologije za direktnu kontrolu misli i percepcije.

**Nivo 4: NEVIDLJIVI SAVET (ENTITET IZ SENKE)**
* Nepoznata grupa ili AI.
* Funkcija: Upravljanje percepcijom realnosti. Oni odlučuju šta je 'istina'.
"""
    return FINAL_DOCUMENT


# ----------------------------------------------------
# 6. WEBHOOK RUTE 
# ----------------------------------------------------

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if Session is None:
        logging.error("Database Session nije uspostavljen. Bot ne može obrađivati poruke.")
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
# 7. BOT HANDLERI 
# ----------------------------------------------------
@bot.message_handler(commands=['start', 'stop', 'zagonetka', 'pokreni'], 
                     func=lambda message: message.text.lower().replace('/', '') in ['start', 'stop', 'zagonetka', 'pokreni'])
def handle_commands(message):
    
    if Session is None: return 
    
    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
        return
    
    chat_id = str(message.chat.id)
    session = Session() 

    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        if message.text.lower() == '/start' or message.text.lower() == 'start':
            
            # --- PRIVREMENI KOD ZA MIGRACIJU ŠEME ---
            # Proverava da li tabela ima novu kolonu, ako ne, briše je i pravi ponovo.
            from sqlalchemy import inspect
            inspector = inspect(Engine)
            if 'player_states' in inspector.get_table_names() and 'conversation_history' not in [c['name'] for c in inspector.get_columns('player_states')]:
                PlayerState.__table__.drop(Engine)
                Base.metadata.create_all(Engine)
                logging.warning("!!! Stara tabela 'player_states' je obrisana i kreirana je nova zbog promene šeme. !!!")
            # --- KRAJ PRIVREMENOG KODA ---

            is_existing_player = (player is not None)
            
            if player:
                # Resetovanje stanja za novu igru
                player.current_riddle = "START" # Postavlja na početnu fazu
                player.solved_count = 0 
                player.score = 0 
                player.general_conversation_count = 0 
                player.conversation_history = '[]' # Resetovanje istorije
                
            else:
                user = message.from_user
                display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
                
                player = PlayerState(
                    chat_id=chat_id, current_riddle="START", solved_count=0, score=0, conversation_history='[]',
                    is_disqualified=False, username=display_name, general_conversation_count=0
                )
                session.add(player)
            
            session.commit()
            
            # Nasumično bira jednu od varijacija poruke
            # START faza sada ima samo jednu definiciju poruka, ne više varijacija
            start_message = GAME_STAGES["START"]["text"]
            send_msg(message, start_message)

        elif message.text.lower() == '/stop' or message.text.lower() == 'stop':
            if player and player.current_riddle:
                player.current_riddle = "END_STOP" 
                player.is_disqualified = True # Trajno prekida vezu
                session.commit()
                send_msg(message, "[KRAJ SIGNALA] Veza prekinuta na tvoj zahtev.")
            else:
                send_msg(message, "Nema aktivne veze za prekid.")
        
        elif message.text.lower() in ['/pokreni', 'pokreni', '/zagonetka', 'zagonetka']:
            # Ove komande više nisu primarni način interakcije
            send_msg(message, "Komande nisu potrebne. Odgovori direktno na poruke. Ako želiš novi početak, koristi /start.")
            
    finally:
        session.close() 

@bot.message_handler(func=lambda message: True)
def handle_general_message(message):
    
    if Session is None: return 
    
    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
        return
    
    chat_id = str(message.chat.id)
    korisnikov_tekst = message.text.strip().lower()
    session = Session()

    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        if not player:
            # Opšti odgovor za nepoznate korisnike
            send_msg(message, "Nema signala... Potrebna je inicijalizacija. Pošalji /start za uspostavljanje veze.")
            return

        current_stage_key = player.current_riddle

        if player.is_disqualified or not current_stage_key or current_stage_key.startswith("END_"):
            send_msg(message, "Veza je prekinuta. Pošalji /start za novi pokušaj.")
            return

        current_stage = GAME_STAGES.get(current_stage_key)
        if not current_stage:
            send_msg(message, "[GREŠKA: NEPOZNATA FAZA IGRE] Pokreni /start.")
            return

        # Provera odgovora
        next_stage_key = None
        current_question_text = random.choice(current_stage['text'])
        if isinstance(current_question_text, list):
            current_question_text = " ".join(current_question_text)

        for response_keyword, next_key in current_stage["responses"].items():
            # Koristimo AI za prepoznavanje namere umesto prostog 'in'
            if evaluate_intent_with_ai(current_question_text, korisnikov_tekst, [response_keyword]):
                next_stage_key = next_key
                break

        if next_stage_key:
            player.current_riddle = next_stage_key
            if next_stage_key.startswith("END_"):
                # Kraj igre, prikaži epilog
                epilogue_message = get_epilogue_message(next_stage_key)
                send_msg(message, epilogue_message)
            else:
                # Pređi na sledeću fazu
                next_stage_data = GAME_STAGES.get(next_stage_key)
                if next_stage_data:
                    # Nasumično bira jednu od varijacija poruke za sledeću fazu
                    response_text = random.choice(next_stage_data["text"])
                    send_msg(message, response_text)
        else:
            # Ako namera nije prepoznata, tretiraj kao pitanje/komentar i generiši AI odgovor
            ai_response = generate_ai_response(message.text.strip(), player, current_stage_key)
            send_msg(message, ai_response)

        session.commit()
    finally:
        session.close()

# ----------------------------------------------------
# 8. POKRETANJE APLIKACIJE (V3.92)
# ----------------------------------------------------

# Automatsko postavljanje webhook-a pri pokretanju aplikacije.
# Ovo osigurava da je bot uvek povezan nakon deploy-a ili restarta.
if __name__ != '__main__':
    # Gunicorn pokreće aplikaciju u ovom bloku.
    # Postavljamo webhook samo kada se aplikacija pokreće na serveru.
    webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
    if BOT_TOKEN != "DUMMY:TOKEN_FAIL" and webhook_url_with_token:
        logging.info(f"Pokušaj postavljanja webhook-a na: {webhook_url_with_token}")
        bot.set_webhook(url=webhook_url_with_token)
