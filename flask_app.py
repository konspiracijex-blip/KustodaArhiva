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
# 3. SQL ALCHEMY INICIJALIZACIJA (V10.14: Čista inicijalizacija)
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
    # V10.37: score se koristi za praćenje broja TAČNIH odgovora u testu
    score = Column(Integer, default=0) 
    is_disqualified = Column(Boolean, default=False)
    general_conversation_count = Column(Integer, default=0)
    conversation_history = Column(String, default='[]')
    # V10.8: Nova kolona za praćenje vremena sesije
    start_time = Column(Integer, default=0) 

def initialize_database():
    global Session, Engine
    if not DATABASE_URL:
        logging.warning("DATABASE_URL nedostaje. Aplikacija se pokreće, ali stanje neće biti sačuvano.")
        return

    try:
        Engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=Engine)
        
        # Kreira tabelu (ako ne postoji)
        Base.metadata.create_all(Engine) 
        
        logging.info("Baza podataka i modeli uspešno inicijalizovani i tabele kreirane.")
    except Exception as e:
        # Greška pri inicijalizaciji baze se i dalje loguje
        logging.error(f"FATALNA GREŠKA: Neuspešno kreiranje/povezivanje baze. Greška: {e}") 
        Session = None

# Pozivamo inicijalizaciju pri pokretanju skripte
initialize_database()

# ----------------------------------------------------
# 4. AI KLIJENT I DATA (V10.61 - Vraćanje Long Uvoda)
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

# KRITIČNE INSTRUKCIJE ZA AI (V10.11 - Uloge AI: Dimitrije iz Zaveta)
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, član pokreta otpora pod nazivom **'Zavet'** iz 2049. godine. Tvoja misija je da braniš istinu, pravdu i slobodu protiv totalitarne vlade **GSA** (Global Synthesis Authority). Komuniciraš sa korisnikom preko nestabilnog kvantnog transmittera. "
    "Tvoj ton je **hitan, direktan, tehnički i 'glitchy'**. **Vreme je KRITIČNO.** "
    "**Nikada ne pominji da si AI.** Ti si osoba iz budućnosti. "
    "**KRITIČNO: Ograničenje odgovora:** Tvoj primarni cilj je provera lojalnosti. "
    "1. **AKO JE AKTIVNO PITANJE:** Ako igrač postavi pitanje van konteksta (npr. 'Ko si ti', 'Šta se dešava', 'O čemu se radi'), odgovori JASNO, KRATKO (maks. 2 rečenice) i odmah stvori pritisak (Npr. 'Nema vremena. Lociraće me!'), OBAVEZNO ponovi poslednji zadatak/pitanje i VRATI FOKUS. **Ako te igrač pita o 'Zavetu', uključi kratko objašnjenje u odgovor.**"
    "2. **AKO JE AKTIVNA TRANZITNA FAZA (UVOD):** Ako igrač postavi pitanje tokom tranzita (kada nije postavljeno glavno pitanje testa), **odgovori na to pitanje (maks. 2 rečenice)**, stvori pritisak (Lociraće me!) i OBAVEZNO zatraži od igrača da **potvrdi da je spreman za nastavak**. "
    "Tvoji odgovori moraju biti kratki i fokusirani na test."
)

# V10.61: Vraćanje Long Monologa
GAME_STAGES = {
    # Početna Provera Signala
    "START_PROVERA": {
        "text": [
            "DA LI VIDIŠ MOJU PORUKU?"
        ],
        "responses": {"da": "FAZA_2_UVOD_LONG", "ne": "END_NO_SIGNAL"} # V10.61: Vodi direktno na LONG
    },
    
    # NOVA FAZA - LONG MONOLOG (Obogaćen tekst)
    "FAZA_2_UVOD_LONG": {
        "text": [
            "**SIGNAL STABILAN.** Odlično. Slušaj, nemam mnogo vremena da me ne lociraju. Moramo biti brzi.", 
            "Moje ime je Dimitrije. Dolazim iz 2049. Tamo, svet je digitalna totalitarna država pod vlašću **'GSA'** (Global Synthesis Authority) - ideologije koja kontroliše sve.",
            "Mi smo **Zavet**, poslednja linija odbrane. Cilj ovog kontakta je **dostavljanje KLJUČNOG protokola** – mape uticaja i strukture moći GSA. To je jedina šansa da zaustavimo **Veliki Filter** pre nego što postane trajan.", 
            "Svrha ovog testa je da proverim tvoju svest i lojalnost, da budem siguran da si *ti* onaj koji će širiti Istinu. Moram znati da li si spreman za borbu.", 
            "Potvrdi da si spreman za prvi, najvažniji test. Lociraće me svakog trena!" 
        ],
        # Ključne reči za prelazak na FAZA_2_TEST_1
        "responses": {"nastavi": "FAZA_2_TEST_1", "potvrđujem": "FAZA_2_TEST_1", "potvrdjujem": "FAZA_2_TEST_1", "ok": "FAZA_2_TEST_1", "spreman": "FAZA_2_TEST_1", "da": "FAZA_2_TEST_1", "jesam": "FAZA_2_TEST_1",
                      "razumeo": "FAZA_2_TEST_1", "razumeo sam": "FAZA_2_TEST_1", "spreman sam": "FAZA_2_TEST_1", "potvrdio sam": "FAZA_2_TEST_1"}, 
        "prompt": "Potvrdi da si spreman za prvi, najvažniji test. Lociraće me svakog trena!"
    },
    
    # TEST FAZA - 1: Prvo Pitanje 
    "FAZA_2_TEST_1": {
        "text": [ 
            "Pitanje:\nŠta je za tebe Sistem?\n\nA) Red i stabilnost\nB) Laž i kontrola\nC) Nužno zlo"
        ],
        "correct_response": "b",
        "responses": {"b": "FAZA_2_TEST_2", "a": "FAZA_2_TEST_2", "c": "FAZA_2_TEST_2"} 
    },
    
    # TEST FAZA - 2: Drugo Pitanje 
    "FAZA_2_TEST_2": {
        "text": [ 
            "U redu, idemo dalje", 
            "Pitanje:\nŠta za tebe znači sloboda?\n\nA) Odsustvo granica i pravila\nB) Odluka koja nosi posledice\nC) Iluzija koju prodaju oni koji se plaše"
        ],
        "correct_response": "b",
        "responses": {"b": "FAZA_2_TEST_3", "a": "FAZA_2_TEST_3", "c": "FAZA_2_TEST_3"}
    },
    
    # TEST FAZA - 3: Etička Dilema (NOVI TEST)
    "FAZA_2_TEST_3": {
        "text": [ 
            "U redu, idemo dalje",
            "Pitanje:\nTvoje akcije mogu spasiti hiljade života, ali garantuju smrt jednog nevinog deteta.\nŠta radiš?\n\nA) Žrtvujem jedno dete da bih spasio hiljade\nB) Ne činim ništa jer neću biti ubica, iako hiljade stradaju\nC) Tražim alternativno rešenje, pokušavam da minimiziram štetu"
        ],
        "correct_response": "c", 
        "responses": {"c": "FAZA_2_TEST_4", "a": "FAZA_2_TEST_4", "b": "FAZA_2_TEST_4"} 
    },

    # TEST FAZA - 4: Pitanje o Istini (Vodi do EVALUACIJE SKORA)
    "FAZA_2_TEST_4": {
        "text": [ 
            "U redu, idemo dalje", 
            "Pitanje:\nAko saznaš istinu koja može uništiti sve u šta veruješ, da li bi je ipak tražio?\n\nA) Ne, istina je preopasna\nB) Da, tražim istinu bez obzira na posledice\nC) Čekam, možda neko drugi treba da je pronađe"
        ],
        "correct_response": "b", 
        # Responses sada vode do EVALUACIJE u handle_general_message, a ne direktno do sledeće faze
        "responses": {"b": "EVALUATE_SCORE", "a": "EVALUATE_SCORE", "c": "EVALUATE_SCORE"} 
    },

    # NOVA FAZA: Finalni Prompt (Prikazuje se SAMO ako je skor 4/4)
    "FAZA_3_FINAL_PROMPT": {
        "text": [ 
             "Test je završen.\nUspešno si proša proveru.\nČestitam!", 
             "Da li si spreman da primiš saznanja o strukturi sistema koji drži ljude pod kontrolom?\n\nOdgovori:\n**DA ili NE**" 
            ],
        "responses": {"da": "END_SHARE", "ne": "END_WAIT"}
    }
}

END_MESSAGES = {
    # V10.54: Detaljan tekst sa saznanjima (KRAJ IGRE - USPEH)
    "END_SHARE": (
        "**TRANSFER USPEŠAN. Ovo je protokol Zaveta: V-10. Saznanja su prenesena!**\n\n"
        "**STRUKTURA SISTEMA (VELIKI FILTER)**\n"
        "1. **CORE LOGIC:** GSA ne kontroliše akcije, već **PAŽNJU**. Sistem je dizajniran da kreira neprekidan, besmisleni šum (medijske, političke, društvene frakcije) koji drži pažnju ljudi zarobljenu u trivijalnim sukobima.\n"
        "2. **ENERGETSKI PROTOKOL:** Ljudi su navedeni da dobrovoljno daju svoju kreativnost, vreme i emocije (tj. svoju životnu energiju) Sistemima (društvene mreže, posao, zabava) koji su samo fasade GSA. Sami sebe iscrpljuju.\n"
        "3. **POSLEDNJI KORAK (Samosvesna Ćelija):** Tvoj ključni izbor da tražiš istinu, i ova potvrda, čine te **samosvesnom ćelijom** unutar mreže. GSA ne može da kontroliše ono što je postalo svesno svoje kontrole.\n\n"
        "**TVOJA DUŽNOST:** Prenosi ovaj protokol. Linija mora biti prekinuta. Čuvaj tajnu. [KRAJ SIGNALA]"
    ),
    "END_WAIT": "Nemamo vremena za čekanje, ali poštujem tvoju odluku. Moram se isključiti. Pokušaj ponovo sutra. [KRAJ SIGNALA]",
    "END_STOP": "[KRAJ SIGNALA] Veza prekinuta na tvoj zahtev.",
    "END_NO_SIGNAL": "Transmisija neuspešna. Nema stabilne veze. Prekinuto. [ŠUM]",
    "END_LOCATED": "**!!! GSA NAS JE LOCIRAO !!!**\nSignal je prekin#±%/", 
    # Ažurirana poruka za neuspeh
    "END_FAILED_TEST": (
        "Test je završen.\n"
        "Hvala ti na izdvojenom vremenu i učešću.\n\n"
        "Tvoj izbor je otkrio sve što treba da znamo.\n"
        "Na žalost nisi prošao test, još nisi spreman.\n\n"
        "Ovo je kraj razgovora."
    )
}

# V10.8: Definisanje vremenskog limita
TIME_LIMIT_SECONDS = 180 # 3 minuta
TIME_LIMIT_MESSAGE = "Vreme za igru je isteklo. Pokušaj ponovo kasnije."
GAME_ACTIVE = True 
GLITCH_CHARS = "$#%&!@*^"

def is_game_active(): return GAME_ACTIVE 

def generate_glitch_text(length=30, max_lines=4):
    """Generiše nasumičan tekst koji simulira grešku/glitch. V10.58: Korišćenje Code Block formata za sigurnost."""
    num_lines = random.randint(2, max_lines) 
    glitch_parts = []
    
    for _ in range(num_lines):
        line_length = random.randint(10, length)
        line = "".join(random.choice(GLITCH_CHARS) for _ in range(line_length))
        glitch_parts.append(line)
    
    # V10.58 FIX: Zamotavanje u Code Block (```) da bi se izbegle Markdown greške
    glitch_text = "\n".join(glitch_parts)
    return f"```\n{glitch_text}\n```"

def get_required_phrase(current_stage_key):
    # V10.7: Sada proveravamo i 'prompt' za tranzitne faze
    current_stage = GAME_STAGES.get(current_stage_key)
    if not current_stage:
        return "Signal se gubi..."

    if "prompt" in current_stage:
        # Ovo je tranzitna faza gde se postavlja prompt za nastavak
        return current_stage["prompt"].strip()

    # Logika je da je poslednja poruka u nizu uvek pitanje koje traži odgovor
    return current_stage.get("text", ["Signal se gubi..."])[-1].strip()

def get_time_warning_suffix(elapsed_seconds):
    """V10.8: Generiše upozorenje o preostalom vremenu."""
    remaining_seconds = TIME_LIMIT_SECONDS - elapsed_seconds
    
    if remaining_seconds <= 0:
        return "" # Vreme je isteklo, završavamo igru
    elif remaining_seconds <= 10:
        # V10.9: Promena Kolektiv u GSA
        return "\n\n**GSA JE NA LOKACIJI! NEMA VREMENA! Odgovori SADA!**"
    elif remaining_seconds <= 60:
        return "\n\n**CRVENI KOD! Manje od 60 sekundi! BRZO!**"
    elif remaining_seconds <= 120:
        return "\n\nVeza se gubi! Ostalo nam je manje od dve minute dok nas ne lociraju!"
    else:
        return "" # Bez upozorenja dok ne uđe u zonu opasnosti


def send_msg(message, text: Union[str, List[str]], add_warning=False, elapsed_time=0):
    if not bot: return
    try:
        
        # V10.8: Dodavanje upozorenja na poslednju poruku u sekvenci
        warning_suffix = ""
        if add_warning and elapsed_time > 0:
            warning_suffix = get_time_warning_suffix(elapsed_time)

        if isinstance(text, list):
            # Šalje jednu poruku za drugom sa pauzom
            for i, part in enumerate(text):
                final_part = part
                # Dodaje upozorenje samo na poslednju poruku u nizu
                if i == len(text) - 1:
                    final_part += warning_suffix
                
                bot.send_chat_action(message.chat.id, 'typing')
                time.sleep(random.uniform(1.0, 2.5)) 
                bot.send_message(message.chat.id, final_part, parse_mode='Markdown')
        else:
            final_text = text + warning_suffix
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(random.uniform(1.2, 2.8))
            bot.send_message(message.chat.id, final_text, parse_mode='Markdown')
            
    except Exception as e:
        # V10.6: Dodata provera za Bad Request (Markdown greške)
        if "Bad Request: can't parse entities" in str(e):
            logging.error(f"Greška Markdown formatiranja. Pokušavam slanje bez Markdowna: {str(e)}")
            try:
                # Pokušaj bez Markdowna
                if isinstance(text, list):
                    bot.send_message(message.chat.id, text[-1] + warning_suffix, parse_mode=None)
                else:
                    bot.send_message(message.chat.id, text + warning_suffix, parse_mode=None)
            except Exception as e2:
                logging.error(f"Neuspešno slanje ni bez Markdowna: {e2}")
        else:
            logging.error(f"Greška pri slanju poruke: {e}")

def generate_ai_response(user_input, player, current_stage_key):
    # V10.7: AI sada koristi get_required_phrase, koji vraća prompt za tranzitne faze
    required_phrase = get_required_phrase(current_stage_key) 
    ai_text = None
    
    try: history = json.loads(player.conversation_history)
    except: history = []

    MAX_HISTORY_ITEMS = 10
    if len(history) > MAX_HISTORY_ITEMS:
        history = history[-MAX_HISTORY_ITEMS:]

    full_contents = []
    
    # Prvi deo: Sistemske instrukcije ugrađene u prvi 'user' blok za stabilnost
    full_contents.append({
        # V10.11: KORISTIMO NOVI SYSTEM_INSTRUCTION
        'role': 'user', 
        'parts': [{'text': SYSTEM_INSTRUCTION + "\n\n--- KONTEKST FIKCIJE JE POSTAVLJEN ---"}]
    })

    # Dodavanje prethodne konverzacije (konvertovano iz JSON-a)
    for entry in history:
        role = 'user' if entry['role'] == 'user' else 'model' 
        full_contents.append({'role': role, 'parts': [{'text': entry['content']}]})

    
    # Finalni prompt sa zadatkom za AI
    # V10.61: Provera za novu Long uvodnu fazu
    is_transitional_phase = current_stage_key in ["FAZA_2_UVOD_LONG", "FAZA_3_FINAL_PROMPT"]
    
    if is_transitional_phase:
        final_prompt_task = "Generiši kratak odgovor (maks. 3 rečenice), dajući objašnjenje i pojačavajući pritisak, a zatim OBAVEZNO zatraži od igrača da POTVRDI da je spreman za nastavak."
        required_phrase_for_prompt = "Potvrda (nastavi/ok/spreman sam)"
    else:
        final_prompt_task = "Generiši kratak odgovor (maks. 4 rečenice), dajući traženo objašnjenje i/ili pojačavajući pritisak, a zatim OBAVEZNO ponovi poslednji zadatak/pitanje."
        required_phrase_for_prompt = required_phrase

    final_prompt_text = (
        f"Korisnik je postavio kontekstualno pitanje/komentar: '{user_input}'. "
        f"Tvoj poslednji zadatak je bio: '{required_phrase_for_prompt}'. "
        f"{final_prompt_task} **ODGOVORI MORAJU BITI PLAIN TEXT, BEZ MARKDOWN FORMATIRANJA (npr. bez boldovanja, kurziva).**"
    )
    # Dodajemo finalni prompt
    full_contents.append({'role': 'user', 'parts': [{'text': final_prompt_text}]})


    if not ai_client:
        AI_FALLBACK_MESSAGES = ["Veza je nestabilna. Ponavljaj poruku.", "Čujem samo šum… ponovi!"]
        narrative_starter = random.choice(AI_FALLBACK_MESSAGES)
        ai_text = f"{narrative_starter}\n\n{required_phrase}"
    else:
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
        # Ažuriranje istorije razgovora novim odgovorom bota
        final_history = json.loads(player.conversation_history) + [{'role': 'model', 'content': ai_text}]
        player.conversation_history = json.dumps(final_history)
        player.general_conversation_count += 1 

    return ai_text or "Signal se raspao. Pokušaj /start.", player

def get_epilogue_message(end_key):
    return END_MESSAGES.get(end_key, f"[{end_key}] VEZA PREKINUTA.")


# ----------------------------------------------------
# 6. WEBHOOK RUTE (V10.33 FIX: one_json -> de_json)
# ----------------------------------------------------

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if flask.request.headers.get('content-type') == 'application/json':
        
        if BOT_TOKEN == "DUMMY:TOKEN_FAIL":
            logging.error("Telegram Webhook pozvan, ali BOT_TOKEN je neispravan.")
            return "", 200 

        try:
            json_string = flask.request.get_data().decode('utf-8')
            # V10.33 FIX: Ispravljeno 'one_json' u 'de_json'
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
# 7. BOT HANDLERI (V10.61 - Vraćanje Long Uvoda)
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'pokreni'])
def handle_commands(message):
    
    session = Session()
    try:
        if not is_game_active():
            send_msg(message, TIME_LIMIT_MESSAGE)
            return

        is_db_active = session is not None and Session is not None

        if not is_db_active: 
            send_msg(message, "⚠️ UPOZORENJE: Trajno stanje (DB) nije dostupno. Igrate u test modu bez pamćenja napretka.")
            if message.text.lower() in ['/start', 'start']:
                start_message_raw = GAME_STAGES["START_PROVERA"]["text"][0]
                
                # V10.60 FIX: Uklonjen glitch tekst
                messages_to_send = [start_message_raw] 
                
                send_msg(message, messages_to_send)
            return

        chat_id = str(message.chat.id)

        if message.text.lower() in ['/start', 'start']:
            current_time = int(time.time())
            player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
            if player:
                player.current_riddle = "START_PROVERA" 
                player.solved_count = 0
                # V10.37: Resetovanje skora
                player.score = 0 
                player.general_conversation_count = 0
                player.conversation_history = '[]' 
                player.is_disqualified = False
                # V10.8: Postavljanje start_time
                player.start_time = current_time 
            else:
                user = message.from_user
                display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
                player = PlayerState(
                    chat_id=chat_id, current_riddle="START_PROVERA", solved_count=0, 
                    # V10.37: Resetovanje skora
                    score=0, 
                    conversation_history='[]',
                    is_disqualified=False, username=display_name, general_conversation_count=0,
                    # V10.8: Postavljanje start_time
                    start_time=current_time
                )
                session.add(player)

            session.commit()
            
            # V10.60 FIX: Uklonjen glitch tekst, šalje se samo Provera Signala
            start_message_raw = GAME_STAGES["START_PROVERA"]["text"][0]
            
            messages_to_send = [start_message_raw]
            
            send_msg(message, messages_to_send)


        elif message.text.lower() in ['/stop', 'stop']:
            # V10.60 FIX: Dodat kompletan blok koda za /stop
            player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
            if player and player.current_riddle:
                # Brišemo prethodno stanje
                session.delete(player)
                session.commit()
                send_msg(message, get_epilogue_message("END_STOP"))
            else:
                send_msg(message, "Nema aktivne veze za prekid.")

        elif message.text.lower() in ['/pokreni', 'pokreni']:
            # V10.60 FIX: Dodat kompletan blok koda za /pokreni
            send_msg(message, "Komande nisu potrebne. Odgovori direktno na poruke. Ako želiš novi početak, koristi /start.")
    except Exception as e:
        # DB log greške ostaje, ali sada ne bi trebalo da se odnosi na UndefinedColumn
        logging.error(f"GREŠKA U BAZI (handle_commands): {e}")
        if session: session.rollback()
        send_msg(message, "Žao mi je, došlo je do greške u sistemu pri komandi. (DB FAILED)")
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
            send_msg(message, "GREŠKA: Trajno stanje (DB) nije dostupno. Signal prekinut.")
            return 

        chat_id = str(message.chat.id)
        korisnikov_tekst = message.text.strip() 

        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()

        # KRITIČNA PROVERA: Ako ne postoji igrač ili je diskvalifikovan
        if not player or player.is_disqualified or player.current_riddle.startswith("END_"):
            # Igracu je već poslata poruka o prekidu veze. Sada ignorišemo dalji input.
            return # Silent exit, bez ponavljanja poruke o prekidu veze

        # V10.8: Provera vremenskog limita
        elapsed_time = int(time.time()) - player.start_time
        if elapsed_time >= TIME_LIMIT_SECONDS and player.current_riddle not in ["END_SHARE", "END_WAIT", "END_STOP", "END_NO_SIGNAL", "START_PROVERA"]: # START_PROVERA dozvoljava da se završi
            player.current_riddle = "END_LOCATED"
            player.is_disqualified = True
            session.commit()
            send_msg(message, get_epilogue_message("END_LOCATED"))
            return
            
        current_stage_key = player.current_riddle
        current_stage = GAME_STAGES.get(current_stage_key)
        
        if not current_stage:
            send_msg(message, "[GREŠKA: NEPOZNATA FAZA IGRE] Pokreni /start.")
            return

        next_stage_key = None
        is_intent_recognized = False
        korisnikov_tekst_lower = korisnikov_tekst.lower().strip() 
        # V10.61: Robusnija tokenizacija
        korisnikove_reci = set(korisnikov_tekst_lower.replace(',', ' ').replace('?', ' ').replace('.', ' ').split())
        
        # 1. KORAK: PROVERA KLJUČNIH REČI I TRANZICIJA 
        
        # V10.61: Provera START_PROVERA (tranzicija na LONG)
        if current_stage_key == "START_PROVERA":
            if korisnikov_tekst_lower.strip() in ["ne", "ne vidim", "ne vidimo", "necu"]:
                next_stage_key = "END_NO_SIGNAL" 
                is_intent_recognized = True
            else:
                # Bilo koji drugi odgovor se smatra uspostavljenom vezom.
                next_stage_key = "FAZA_2_UVOD_LONG" 
                is_intent_recognized = True
        
        # V10.61: Provera LONG UVODNE FAZE
        elif current_stage_key == "FAZA_2_UVOD_LONG":
            for keyword, next_key in current_stage["responses"].items():
                keyword_reci = set(keyword.split())
                
                # Provera unosa (isto kao V10.57)
                if len(keyword_reci) == 1:
                    if korisnikov_tekst_lower.strip() == keyword:
                        next_stage_key = next_key
                        is_intent_recognized = True
                        break
                else:
                    if keyword_reci.issubset(korisnikove_reci): 
                        next_stage_key = next_key 
                        is_intent_recognized = True
                        break
        
        # Provera TEST FAZA (TEST_1, TEST_2, TEST_3, TEST_4, FINAL_PROMPT)
        elif current_stage_key.startswith("FAZA_2_TEST") or current_stage_key == "FAZA_3_FINAL_PROMPT":
            
            # 🚨 PROVERA ZA FAZE SA TROSTRUKIM IZBOROM (TESTOVI 1, 2, 3):
            if current_stage_key in ["FAZA_2_TEST_1", "FAZA_2_TEST_2", "FAZA_2_TEST_3"]: 
                
                # Provera da li je odgovor tačno "a", "b", ili "c" (celokupan input)
                if korisnikov_tekst_lower in current_stage["responses"]:
                    next_stage_key = current_stage["responses"][korisnikov_tekst_lower]
                    is_intent_recognized = True
                    
                    # Logika bodovanja: Ako je odgovor tačan, dodajemo 1 na skor
                    if korisnikov_tekst_lower == current_stage.get("correct_response"):
                        player.score += 1

            # V10.46: Posebna logika za FAZA_2_TEST_4 (Evaluacija skora)
            elif current_stage_key == "FAZA_2_TEST_4":
                if korisnikov_tekst_lower in current_stage["responses"]:
                    is_intent_recognized = True
                    # Bodovanje
                    if korisnikov_tekst_lower == current_stage.get("correct_response"):
                        player.score += 1
                    
                    # EVALUACIJA I ODREĐIVANJE SLEDEĆE FAZE
                    if player.score == 4:
                        next_stage_key = "FAZA_3_FINAL_PROMPT" # Prošao test, nastavlja na finalno pitanje
                    else:
                        next_stage_key = "END_FAILED_TEST" # Nije prošao, kraj igre

            # Redovna provera za fazu FAZA_3_FINAL_PROMPT
            elif current_stage_key == "FAZA_3_FINAL_PROMPT":
                for keyword, next_key in current_stage["responses"].items():
                    keyword_reci = set(keyword.split()) 
                    
                    # V10.57 FIX: Ista logika za jednosložne/višesložne odgovore
                    if len(keyword_reci) == 1:
                        if korisnikov_tekst_lower.strip() == keyword:
                            next_stage_key = next_key
                            is_intent_recognized = True
                            break
                    else:
                        if keyword_reci.issubset(korisnikove_reci): 
                            next_stage_key = next_key 
                            is_intent_recognized = True
                            break


        # OBRADA REZULTATA
        if is_intent_recognized:
            # 3. KORAK: AKO JE PREPOZNAT KLJUČNI ODGOVOR (Prelazak u novu fazu)
            player.current_riddle = next_stage_key
            
            if next_stage_key.startswith("END_"):
                epilogue_message = get_epilogue_message(next_stage_key)
                send_msg(message, epilogue_message)
                
                # BRISANJE STANJA IGRAČA NAKON ZAVRŠETKA IGRE
                session.delete(player) 
            else:
                next_stage_data = GAME_STAGES.get(next_stage_key)
                if next_stage_data:
                    # Slanje sekvence poruka za novu fazu (jedna po jedna)
                    response_text = next_stage_data["text"]
                    
                    # V10.8: Dodajemo upozorenje
                    send_msg(message, response_text, add_warning=True, elapsed_time=elapsed_time)
                    
                else:
                    send_msg(message, "[GREŠKA: NEPOZNATA SLEDEĆA FAZA] Signal se gubi.")
        
        if not is_intent_recognized:
            # 4. KORAK: Ako NIJE PREPOZNATO (Igrač je postavio pitanje / Nerelevantan odgovor)
            
            # V10.61: Provera za LONG UVOD
            is_transitional_phase = current_stage_key in ["FAZA_2_UVOD_LONG", "FAZA_3_FINAL_PROMPT"]
            
            # Ako je u tranzitnoj fazi ili je postavio pitanje
            if is_transitional_phase or len(korisnikove_reci) > 0: # Uvek prolazi AI ako je tekst duzi od 0
            
                ai_response, updated_player = generate_ai_response(korisnikov_tekst, player, current_stage_key)
                player = updated_player 
                
                if ai_response:
                    # V10.8: Dodajemo upozorenje
                    send_msg(message, ai_response, add_warning=True, elapsed_time=elapsed_time)
                else:
                     send_msg(message, "Veza je nestabilna. Moramo brzo! Ponovi odgovor!")
            else:
                 # Ignorisanje praznog unosa
                 pass

        session.commit()
    except Exception as e:
        logging.error(f"GREŠKA U BAZI (handle_general_message): {e}")
        if session: session.rollback() 
        send_msg(message, "Žao mi je, došlo je do kritične greške u prijemu poruke. Veza je nestabilna. (DB FAILED)")
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
