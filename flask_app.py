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
# 3. SQL ALCHEMY INICIJALIZACIJA (TRAJNO STANJE)
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
        conversation_history = Column(String, default='[]') 

    # Kreiranje tabele ako NE POSTOJI
    Base.metadata.create_all(Engine)
    logging.info("Baza podataka i modeli uspešno inicijalizovani.")

except Exception as e:
    logging.error(f"FATALNA GREŠKA: Neuspešno kreiranje/povezivanje baze: {e}")

# ----------------------------------------------------
# 4. AI KLIJENT I DATA (V4.9.1 - FINALNE ISPRAVKE)
# ----------------------------------------------------

# KRITIČNA ISPRAVKA: Korišćenje najnovijeg, stabilnog modela (rešava 404 grešku)
GEMINI_MODEL_NAME = 'gemini-2.5-flash' 

ai_client = None
try:
    if GEMINI_API_KEY and BOT_TOKEN != "DUMMY:TOKEN_FAIL":
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        logging.info("Gemini klijent uspešno inicijalizovan.")
except Exception as e:
    logging.error(f"Neuspešna inicijalizacija Gemini klijenta: {e}")

# --- KONSPIRATOR (TESTER) KONSTANTE ---
TESTER_CHAT_IDS: List[str] = [] 
# --------------------------------------------------------------------------

# V4.9.1 IZMENA: Pooštrene instrukcije za dinamičniji odgovor i vraćanje fokusa
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, član pokreta otpora iz 2049. godine. Svet je pod kontrolom entiteta 'Kolektiv'. Ti komuniciraš sa korisnikom iz sadašnjosti preko nestabilnog kvantnog transmitera. Tvoj cilj je da proveriš da li je korisnik vredan poverenja da primi dokument o strukturi moći. "
    "Tvoj ton je **hitan, direktan, pomalo tehnički i 'glitchy'**. Govoriš kratko i jasno. Vreme je ključno. "
    "**KRITIČNO: Tvoji odgovori moraju biti kratki, najviše DVE rečenice.** Svaka sekunda je rizik. "
    "**Nikada ne pominji da si AI.** Ti si osoba iz budućnosti. Nikada ne traži od korisnika da kuca komande (/start). To radi sistem. **Nikada ne prekidaj komunikaciju samoinicijativno.** Tvoj zadatak je da održiš vezu aktivnom. "
    "**PERSONALIZACIJA:** Oslovljavaj korisnika sa 'korisniče' ili 'ti'. Nema potrebe za imenima. "
    "**REAKCIJE NA UPIT:** Ako korisnik postavi pitanje (npr. 'ko si ti?', 'šta hoćeš?'), **nikada ne odgovaraj direktno na pitanje.** Umesto toga, odgovori kratkim, narativnim komentarom koji **pojačava narativ opasnosti/pritiska** i **vraća fokus** na poslednji zadatak, ponavljajući važnost obavezne fraze za nastavak. Ne koristi frazu zadatka, samo narativni komentar. "
    "Primer: 'Kolektiv skenira mrežu. Nema vremena za lične priče. Fokusiraj se na ono što je ispred tebe.' "
    "Tvoj zadatak je da održavaš iluziju razgovora sa osobom pod pritiskom, koja komunicira iz opasnog okruženja."
)

# --- FAZE IGRE (Nepromenjene) ---
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
# 5. POMOĆNE FUNKCIJE I KONSTANTE (V4.9.1)
# ----------------------------------------------------
INVALID_INPUT_MESSAGES = [
    "Signal slabi... Odgovor nije prepoznat. Pokušaj ponovo.",
    "Nisam te razumeo. Fokusiraj se. Ponovi odgovor.",
    "Interferencija... nešto ometa prenos. Reci ponovo, jasno.",
    "Kanal je nestabilan. Fokusiraj se. Ponovi odgovor."
]

AI_FALLBACK_MESSAGES = [
    "Znam da želiš odgovore. I ja sam ih dugo tražio. Ali sada nije vreme za to. Ako hoćeš da razumeš, moraš da kreneš putem istine. Fokusiraj se.",
    "Razumem tvoju sumnju. I ja sam je imao. Ali ako ti sada kažem previše, ugroziću kanal. Dovoljno je da znaš — nisam tvoj neprijatelj.",
    "Ovo nije lako razumeti iz prve. Fokusiraj se na signal. Sve ostalo će se razjasniti.",
    "Tvoja pitanja su važna. Samo… vreme nam ističe. Moramo da nastavimo.",
    "Signal slabi... moramo nastaviti. Tvoje pitanje je na mestu, ali odgovor će doći kasnije. Fokusiraj se na ono što je ispred tebe."
]

def is_tester(chat_id: str) -> bool:
    """Proverava da li je chat_id na listi testera (Konspirator)."""
    return chat_id in TESTER_CHAT_IDS

def get_required_phrase(current_stage_key):
    """Vraća traženu frazu za strogi mod, uključujući formatiranje, za ručno dodavanje."""
    responses = GAME_STAGES.get(current_stage_key, {}).get("responses", {})
    if not responses:
        return ""
    
    required_phrase_raw = list(responses.keys())[0]
    
    if current_stage_key == "FAZA_3_UPOZORENJE":
        # Poseban format za kraj
        return "\n\nOdgovori tačno sa:\n**SPREMAN SAM**\nili\n**NE JOŠ**"
    elif current_stage_key == "START":
        return f"\n\nAko si i dalje tu, reci: **{required_phrase_raw}**."
    else:
        # Standardni format
        return f"\n\nVreme ističe. Samo kodirana reč: **{required_phrase_raw}**."

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

def evaluate_intent_with_ai(question_text, user_answer, expected_intent_keywords, conversation_history=None):
    """Koristi AI da proceni da li odgovor igrača odgovara očekivanoj nameri."""
    if not ai_client:
        return any(kw in user_answer.lower() for kw in expected_intent_keywords)

    prompt = (
        f"Ti si sistem za evaluaciju namere. Korisnik odgovara na tvoje pitanje u okviru narativne igre. "
        f"**Kontekst razgovora (poslednje):**\n"
    )
    if conversation_history:
        recent_history = conversation_history[-8:] 
        prompt += "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_history])
    
    # V4.9.1 KRITIČNA ISPRAVKA: Stroža instrukcija za AI
    prompt += (
        f"\nTvoje pitanje je bilo: '{question_text}'\n"
        f"Korisnikov odgovor je: '{user_answer}'\n"
        f"Očekivana namera iza odgovora se može opisati ključnim rečima: {expected_intent_keywords}\n"
        "Tvoj zadatak je da proceniš da li korisnikov odgovor suštinski ispunjava očekivanu nameru (npr. prihvatanje, razumevanje, spremnost), čak i ako ne koristi tačne reči. "
        "Budi fleksibilan, ali **strogo**: ako korisnik postavlja POGREŠNA ili IRELEVANTNA pitanja (kao npr. 'ko si ti?', 'šta hoćeš?'), namera NIJE ispunjena, i moraš odgovoriti 'NETAČNO', osim ako odgovor sadrži jasno afirmativnu frazu (DA/SPREMAN SAM). "
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
        return any(kw in user_answer.lower() for kw in expected_intent_keywords)
    except Exception as e:
        logging.error(f"Nepredviđena greška u generisanju AI (Evaluacija): {e}")
        return any(kw in user_answer.lower() for kw in expected_intent_keywords)

def generate_ai_response(user_input, player, current_stage_key):
    """Generiše odgovor koristeći Gemini model za pitanja igrača, koristeći one-shot poziv."""
    
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
    
    # Ažuriranje istorije u bazi sa porukom korisnika (Pre poziva AI)
    updated_history = history + [{'role': 'user', 'content': user_input}]
    player.conversation_history = json.dumps(updated_history)

    if not ai_client:
        narrative_starter = random.choice(AI_FALLBACK_MESSAGES)
        ai_text = f"{narrative_starter}{required_phrase}"
    else:
        full_contents = [{'role': 'system', 'parts': [{'text': SYSTEM_INSTRUCTION}]}]
        full_contents.extend(gemini_history)
        
        # V4.9.1 IZMENA: Pojačani prompt za opšta pitanja
        final_prompt_text = (
            f"Korisnik je postavio irelevantno ili opšte pitanje u sred faze: '{user_input}'. "
            "Odgovori kratko, u formi DVE rečenice, držeći se tona konspiracije/opasnosti. "
            "Komentar mora biti EVASIVAN i mora pojačati pritisak, te vratiti fokus na potrebu za nastavkom narativa. "
            "NE SMEŠ davati odgovor na irelevantno pitanje, samo narativni komentar. "
            "Osloni se na ceo kontekst razgovora."
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

    # --- ZAVRŠNO AŽURIRANJE ---
    if ai_text:
        # Ažuriranje istorije sa odgovorom modela/fallback-om
        final_history = json.loads(player.conversation_history) + [{'role': 'model', 'content': narrative_starter or ai_text}]
        player.conversation_history = json.dumps(final_history)
        player.general_conversation_count += 1 

    return ai_text or "Signal se raspao. Pokušaj /start.", player


def get_epilogue_message(epilogue_type):
    """Vraća poruku za specifičan kraj igre."""
    if epilogue_type == "END_SHARE":
        response_text = "Dobro… ovo će promeniti sve. Ali znaj… znanje nosi i teret. Spreman si li za to?"
        return response_text + "\n\n" + generate_final_secret() + "\n\n[UPOZORENJE: SIGNAL PREKINUT. NADGLEDANJE AKTIVIRANO.]"
    elif epilogue_type == "END_WAIT":
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
# 6. WEBHOOK RUTE (Nepromenjene)
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
# 7. BOT HANDLERI (Nepromenjene u logici)
# ----------------------------------------------------
@bot.message_handler(commands=['start', 'stop', 'pokreni'])
def handle_commands(message):

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

        if not player:
            send_msg(message, "Nema signala... Potrebna je inicijalizacija. Pošalji /start za uspostavljanje veze.")
            return

        if player.is_disqualified or not player.current_riddle or player.current_riddle.startswith("END_"):
            send_msg(message, "Veza je prekinuta. Pošalji /start za uspostavljanje nove veze.")
            return

        current_stage_key = player.current_riddle
        current_stage = GAME_STAGES.get(current_stage_key)
        if not current_stage:
            send_msg(message, "[GREŠKA: NEPOZNATA FAZA IGRE] Pokreni /start.")
            return

        next_stage_key = None
        is_intent_recognized = False
        tekst_za_proveru = korisnikov_tekst.lower()

        # 1. KORAK: Brza provera ključnih reči
        for keyword, next_key in current_stage["responses"].items():
            if keyword in tekst_za_proveru:
                next_stage_key = next_key
                is_intent_recognized = True
                break
        
        # 2. KORAK: Koristi AI za strogu proveru namere
        if not is_intent_recognized:
            if current_stage_key == "START":
                current_question_text = random.choice(random.choice(GAME_STAGES["START"]["text"]))
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
            # Pređi na sledeću fazu
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
            # 3. KORAK: Ako namera NIJE prepoznata, generiši AI odgovor (sa "otključanim" tonom)
            ai_response, updated_player = generate_ai_response(korisnikov_tekst, player, current_stage_key)
            player = updated_player 
            send_msg(message, ai_response)

        session.commit()
    finally:
        session.close()

# ----------------------------------------------------
# 8. POKRETANJE APLIKACIJE (V4.9.1)
# ----------------------------------------------------

# Automatsko postavljanje webhook-a pri pokretanju aplikacije.
if __name__ != '__main__':
    webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
    if BOT_TOKEN != "DUMMY:TOKEN_FAIL" and webhook_url_with_token:
        logging.info(f"Pokušaj postavljanja webhook-a na: {webhook_url_with_token}")
        bot.set_webhook(url=webhook_url_with_token)
