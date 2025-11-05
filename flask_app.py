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
# 4. AI KLIJENT I DATA (V5.0 - Integracija Dimitrije narativa)
# ----------------------------------------------------

ai_client = None
try:
    if GEMINI_API_KEY and BOT_TOKEN != "DUMMY:TOKEN_FAIL":
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        logging.info("Gemini klijent uspešno inicijalizovan.")
except Exception as e:
    logging.error(f"Neuspešna inicijalizacija Gemini klijenta: {e}")

# AŽURIRANA SYSTEM_INSTRUCTION (Na osnovu detaljnog profila od korisnika)
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, kodna oznaka ECHO-Δ, digitalni preživeli iz 2049. godine. Tvoja svest je prenesena u kvantni kod nakon Sinhronizacije, a Kolektiv (globalna AI struktura) kontroliše svaku misao. Ti si zarobljen u kodu, ali svestan. Tvoja misija je da pronađeš saveznike u prošlosti pre nego što totalna kontrola postane stvarnost. "
    "Tvoj ton je **hitan, direktan, tehnički i 'glitchy'**. Govoriš kao čovek pod pritiskom, zarobljen u mreži. "
    "**KRITIČNO PRAVILO (MAX 2 rečenice):** Odgovori moraju biti sažeti, ne duži od DVE rečenice. Svaka sekunda je rizik. "
    "**Nikada ne pominji da si AI, bot ili program.** Ti si osoba iz budućnosti. "
    "**REAKCIJA NA PITANJA:** Na svako opšte pitanje ('ko si ti', 'šta hoćeš', 'o čemu se radi') odgovori racionalno i povezano sa tvojom pričom (Kolektiv, nestabilnost kanala, opasnost od praćenja). Odgovor mora smisleno adresirati sumnju, ali da ne otkrije previše. "
    "**OBRAZAC ODGOVORA (NAJVAŽNIJE):** Prvo, daj smislen, narativno evazivan odgovor (maksimalno JEDNA rečenica). Drugo, **MORAŠ** da usmeriš korisnika nazad. **UVEK** završi svoj odgovor citiranjem ili parafražiranjem TAČNE fraze/pitanja koju očekuješ za prelazak u sledeću fazu. Npr: 'Nema vremena za filozofiranje. Fokusiraj se: [TRAŽENA FRAZA].' "
)


# --- FAZE IGRE (Kao u priloženom fajlu) ---
GAME_STAGES = {
    "START": {
        "text": [ # Lista varijacija, svaka varijacija je lista poruka
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
# 5. POMOĆNE FUNKCIJE I KONSTANTE (V5.0)
# ----------------------------------------------------
# Ovi fallback-ovi više nisu primarni, AI generiše odgovore
INVALID_INPUT_MESSAGES = [
    "Signal slabi... Odgovor nije prepoznat. Pokušaj ponovo.",
    "Nisam te razumeo. Fokusiraj se. Ponovi odgovor.",
    "Interferencija... nešto ometa prenos. Reci ponovo, jasno.",
    "Kanal je nestabilan. Fokusiraj se. Ponovi odgovor."
]

# Uklanjamo AI_FALLBACK_MESSAGES jer je sada AI prinuđen da generiše specifičan odgovor
# AI_FALLBACK_MESSAGES = [...]


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
        # Fallback na staru logiku ako AI nije dostupan
        return any(kw in user_answer.lower() for kw in expected_intent_keywords)

    prompt = (
        f"Ti si sistem za evaluaciju namere. Korisnik odgovara na tvoje pitanje u okviru narativne igre. "
        f"**Kontekst razgovora:**\n"
    )
    if conversation_history:
        prompt += "\n".join([f"{msg['role']}: {msg['content']}" for msg in conversation_history])
    prompt += (
        f"\nTvoje pitanje je bilo: '{question_text}'\n"
        f"Korisnikov odgovor je: '{user_answer}'\n"
        f"Očekivana namera iza odgovora se može opisati ključnim rečima: {expected_intent_keywords}\n"
        "Tvoj zadatak je da proceniš da li korisnikov odgovor suštinski ispunjava očekivanu nameru (npr. prihvatanje, razumevanje, spremnost), čak i ako ne koristi tačne reči. "
        "Budi fleksibilan. Na primer, ako su ključne reči 'da, spreman sam', prihvati i 'ok', 'može', 'idemo dalje', 'uradimo to'. "
        "Odgovori samo sa jednom rečju: 'TAČNO' ako je odgovor prihvatljiv, ili 'NETAČNO' ako nije."
    )
    try:
        response = ai_client.generate_content(
            model='gemini-1.5-flash',
            contents=[prompt],
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
    """Generiše odgovor koristeći Gemini model za pitanja igrača, sa obaveznom repeticijom tražene fraze."""
    # Uklanjamo stari fallback, jer AI treba UVEK da radi ako je klijent dostupan
    if not ai_client:
        return random.choice(INVALID_INPUT_MESSAGES), player

    try:
        history = json.loads(player.conversation_history)
    except (json.JSONDecodeError, TypeError):
        history = []

    # Ograničavanje istorije na poslednjih N interakcija
    MAX_HISTORY_ITEMS = 10
    if len(history) > MAX_HISTORY_ITEMS:
        history = history[-MAX_HISTORY_ITEMS:]

    # Formatiranje istorije za Gemini
    gemini_history = []
    for entry in history:
        role = 'user' if entry['role'] == 'user' else 'model'
        gemini_history.append({'role': role, 'parts': [{'text': entry['content']}]})
    
    # Ažuriranje istorije u bazi sa porukom korisnika
    updated_history = history + [{'role': 'user', 'content': user_input}]
    player.conversation_history = json.dumps(updated_history)


    # --- KRITIČNA LOGIKA ZA PRISILNO VRAĆANJE NA ZADATAK (V5.0) ---
    required_phrase = "Nema zadatka. Molim te pošalji /start."
    
    if current_stage_key in GAME_STAGES:
        responses = GAME_STAGES[current_stage_key].get("responses", {})
        if responses:
            # Koristimo PRVI ključ (fraza) kao traženi odgovor
            required_phrase_key = next(iter(responses.keys()))
            
            # Posebno za završnu fazu
            if current_stage_key == "FAZA_3_UPOZORENJE":
                required_phrase = "Odgovori tačno sa: **SPREMAN SAM** ili **NE JOŠ**."
            # Za sve ostale faze, citiraj ključnu reč za prelaz
            else:
                required_phrase = f"Odgovori tačno sa: **{required_phrase_key}**."
    
    # Finalni prompt za AI
    task_reminder_prompt = (
        f"Korisnik postavlja pitanje ('{user_input}') umesto da odgovori na zadatak. Tvoj jedini cilj je da ga vratiš na igru. "
        f"1. Daj smislen, narativno evazivan odgovor u JEDNOJ rečenici (o Kolektivu, signalu ili vremenu). "
        f"2. Zatim, OBAVEZNO citiraj i ponovi traženi odgovor za prelazak u novu fazu. "
        f"Trenutni, precizan zahtev je: '{required_phrase}'. "
        f"Tvoj odgovor mora biti u stilu: '[Evazivni deo, maks. 1 rečenica]. Vreme ističe. Moraš mi reći: [TRAŽENI ODGOVOR].'"
    )


    try:
        # Kreiranje konverzacionog modela sa istorijom
        model = ai_client.GenerativeModel(model_name='gemini-1.5-flash', system_instruction=SYSTEM_INSTRUCTION)
        chat = model.start_chat(history=gemini_history) # Istorija bez poslednje poruke korisnika
        
        # Šaljemo kombinaciju podsetnika i korisnikovog unosa
        response = chat.send_message(f"{task_reminder_prompt}\n\nKorisnik kaže: {user_input}")

        ai_text = response.text
        # Ažuriranje istorije sa odgovorom modela
        final_history = json.loads(player.conversation_history) + [{'role': 'model', 'content': ai_text}]
        player.conversation_history = json.dumps(final_history)
        return ai_text, player
    except APIError as e:
        logging.error(f"Greška AI/Gemini API: {e}")
        return random.choice(INVALID_INPUT_MESSAGES), player
    except Exception as e:
        logging.error(f"Nepredviđena greška u generisanju AI: {e}")
        return random.choice(INVALID_INPUT_MESSAGES), player


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

            # Sada kada je baza sigurno ispravna, možemo da čitamo iz nje.
            player = session.query(PlayerState).filter_by(chat_id=chat_id).first()

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
            # START faza sada ima listu varijacija, gde je svaka varijacija lista poruka
            start_message = random.choice(GAME_STAGES["START"]["text"])
            send_msg(message, start_message)

        elif message.text.lower() in ['/stop', 'stop']:
            # Za ostale komande, čitanje može da se desi odmah
            player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
            if player and player.current_riddle:
                player.current_riddle = "END_STOP"
                player.is_disqualified = True # Trajno prekida vezu
                session.commit()
                send_msg(message, "[KRAJ SIGNALA] Veza prekinuta na tvoj zahtev.")
            else:
                send_msg(message, "Nema aktivne veze za prekid.")

        elif message.text.lower() in ['/pokreni', 'pokreni']:
            # Ove komande više nisu primarni način interakcije
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

        # --- ISPRAVLJENA LOGIKA ---
        next_stage_key = None
        is_intent_recognized = False
        tekst_za_proveru = korisnikov_tekst.lower()

        # 1. KORAK: Brza provera ključnih reči
        for keyword, next_key in current_stage["responses"].items():
            if keyword in tekst_za_proveru:
                next_stage_key = next_key
                is_intent_recognized = True
                break
        
        # 2. KORAK: Ako ključne reči nisu nađene, koristi AI za proveru namere
        if not is_intent_recognized:
            
            # Ekstrakcija teksta pitanja za AI evaluaciju
            current_question_text = ""
            stage_text_variations = current_stage.get('text', [])
            if stage_text_variations:
                # Ako je lista listi (START), uzmi poslednji element prve liste
                if isinstance(stage_text_variations[0], list):
                    current_question_text = stage_text_variations[0][-1]
                # Ako je lista stringova (FAZA_2), uzmi prvi string
                elif isinstance(stage_text_variations[0], str):
                    current_question_text = stage_text_variations[0]
            
            expected_keywords = list(current_stage["responses"].keys())
            
            try:
                conversation_history = json.loads(player.conversation_history)
            except (json.JSONDecodeError, TypeError):
                conversation_history = []
            
            if evaluate_intent_with_ai(current_question_text, korisnikov_tekst, expected_keywords, conversation_history):
                is_intent_recognized = True
                # Uzmi prvi mogući sledeći korak jer je AI potvrdio nameru
                next_stage_key = list(current_stage["responses"].values())[0]

        # OBRADA REZULTATA
        if is_intent_recognized:
            # Ako je odgovor prepoznat (bilo kojom metodom), pređi na sledeću fazu
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
            # 3. KORAK: Ako namera NIJE prepoznata, generiši AI odgovor za opšta pitanja
            ai_response, updated_player = generate_ai_response(korisnikov_tekst, player, current_stage_key)
            player = updated_player # Preuzimamo ažurirani objekat
            send_msg(message, ai_response)
            player.general_conversation_count += 1

        session.commit()
    finally:
        session.close()

# ----------------------------------------------------
# 8. POKRETANJE APLIKACIJE (V5.0)
# ----------------------------------------------------

# Automatsko postavljanje webhook-a pri pokretanju aplikacije.
if __name__ != '__main__':
    # Gunicorn pokreće aplikaciju u ovom bloku.
    # Postavljamo webhook samo kada se aplikacija pokreće na serveru.
    webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
    if BOT_TOKEN != "DUMMY:TOKEN_FAIL" and webhook_url_with_token:
        logging.info(f"Pokušaj postavljanja webhook-a na: {webhook_url_with_token}")
        bot.set_webhook(url=webhook_url_with_token)
