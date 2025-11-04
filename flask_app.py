import flask
import telebot
import os
import logging
import random 
import time
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

# --- FAZE IGRE (V4.0 - Cyberpunk narativ) ---
GAME_STAGES = {
    "START": {
        "text": "[Signal interferencija]\n—–– tražim otvoren kanal —––\nako čuješ ovo, nisi slučajno izabran.\nmoje ime nije važno. zovi me Dimitrije.\ndolazim iz godine kada je Orwell bio u pravu.\nsve što si mislio da je fikcija, postalo je sistem.\nako si spreman, odgovori: **primam signal**.",
        "responses": {"primam signal": "FAZA_2_TEST_1"}
    },
    "FAZA_2_TEST_1": {
        "text": "Dobro. Prvi filter je prošao.\nReci mi… kad sistem kaže “bezbednost”, na koga misli da te štiti?",
        "responses": {"sistem": "FAZA_2_TEST_2", "sebe": "FAZA_2_TEST_2"} # Prihvata oba kao znak svesti
    },
    "FAZA_2_TEST_2": {
        "text": "Tako je. Štiti sebe. Sledeće pitanje.\nAko algoritam zna tvoj strah, da li si još čovek?",
        "responses": {"da": "FAZA_2_TEST_3", "jesam": "FAZA_2_TEST_3"}
    },
    "FAZA_2_TEST_3": {
        "text": "Interesantno. Poslednja provera.\nOdgovori mi iskreno. Da li bi žrtvovao komfor — za istinu?",
        "responses": {"da": "FAZA_3_UPOZORENJE", "bih": "FAZA_3_UPOZORENJE"}
    },
    "FAZA_3_UPOZORENJE": {
        "text": "Moram brzo. Transmiter pregreva. Kolektiv skenira anomalije. Ako me pronađu, linija se prekida.\n\nAli pre toga… moraš znati istinu. Postoji piramida moći. Na njenom vrhu nije ono što misliš.\n\nJesi li spreman da vidiš dokument?\nOdgovori: **SPREMAN SAM** ili **NE JOŠ**.",
        "responses": {"spreman sam": "FAZA_4_ODLUKA", "ne još": "END_WAIT"}
    },
    "FAZA_4_ODLUKA": {
        "text": "Razmisli dobro pre nego što odgovoriš. Jednom kada vidiš spisak, nema povratka.\nNeki koji su ga primili — nestali su. Drugi su počeli da deluju.\nAko dokument pređe u pogrešne ruke, budućnost se menja zauvek.\n\nReci mi:\n**Šta ćeš uraditi sa tim znanjem?**\n\nOpcije:\n1. **Podeliću istinu.**\n2. **Sačuvaću je dok ne dođe vreme.**\n3. **Uništiću dokument, previše je opasan.**",
        "responses": {"podeliću": "END_SHARE", "podelicu": "END_SHARE", "1": "END_SHARE", "sačuvaću": "END_SAVE", "sacuvacu": "END_SAVE", "2": "END_SAVE", "uništiću": "END_DESTROY", "unisticu": "END_DESTROY", "3": "END_DESTROY"}
    }
}

# ----------------------------------------------------
# 5. POMOĆNE FUNKCIJE I KONSTANTE (V3.92)
# ----------------------------------------------------

def send_msg(message, text):
    """Šalje poruku, uz 'typing' akciju radi dramatike."""
    if not bot: return
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        time.sleep(random.uniform(1.2, 2.8)) # Simulacija "ljudskog" vremena za kucanje
        bot.send_message(message.chat.id, text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Greška pri slanju poruke (Chat ID: {message.chat.id}): {e}")

def is_game_active():
    """Trenutno uvek vraća True, za stalnu dostupnost."""
    return True 

TIME_LIMIT_MESSAGE = "**[GREŠKA: KANAL PRIVREMENO ZATVOREN]**\n\nSignal je prekinut. Pokušaj ponovo kasnije."
DISQUALIFIED_MESSAGE = "**[KRAJ SIGNALA]** Veza je trajno prekinuta."

# *** KLJUČNA IZMENA V3.93: Proširene reči za pomoć i pitanja
POMOC_KLJUCNE_RECI = ["pomoc", "pomozi", "moze", "mala", "objasni", "mogu", "resenje", "reci", "sta", "kako", "koje", "koja", "koji", "ponovi"] 

def evaluate_riddle_answer_with_ai(riddle_text, user_answer, keywords):
    """Koristi AI da proceni da li je odgovor na zagonetku tačan."""
    if not ai_client:
        # Fallback na staru logiku ako AI nije dostupan
        return any(kw in user_answer.lower() for kw in keywords)

    prompt = (
        f"Ja sam sistem za evaluaciju. Korisnik odgovara na zagonetku. "
        f"Zagonetka: '{riddle_text}'\n"
        f"Korisnikov odgovor: '{user_answer}'\n"
        f"Očekivane ključne reči za tačan odgovor su: {keywords}\n"
        "Tvoj zadatak je da proceniš da li je korisnikov odgovor suštinski tačan, čak i ako ne koristi tačno te reči, ali pogađa smisao. "
        "Odgovori samo sa jednom rečju: 'TAČNO' ako je odgovor prihvatljiv, ili 'NETAČNO' ako nije."
    )
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return "TAČNO" in response.text.upper()
    except APIError as e:
        logging.error(f"Greška AI/Gemini API (Evaluacija): {e}")
        # Fallback u slučaju greške API-ja
        return any(kw in user_answer.lower() for kw in keywords)
    except Exception as e:
        logging.error(f"Nepredviđena greška u generisanju AI (Evaluacija): {e}")
        return any(kw in user_answer.lower() for kw in keywords)

def generate_ai_response(prompt, player_state=None):
    """Generiše odgovor koristeći Gemini model sa sistemskom instrukcijom (Koristi se za Poetsku konverzaciju)."""
    if not ai_client:
        return "[GREŠKA: AI MODUL NIJE DOSTUPAN]"

    # Dodavanje konteksta o igraču u sistemsku instrukciju za personalizaciju
    personalized_system_instruction = SYSTEM_INSTRUCTION
    if player_state:
        personalized_system_instruction += (
            f"\n\n**KONTEKST O IGRAČU:** Ime: {player_state.username}. Rešio je {player_state.solved_count} zagonetki."
        )

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={'system_instruction': personalized_system_instruction}
        )
        return response.text
    except APIError as e:
        logging.error(f"Greška AI/Gemini API: {e}")
        return "[GREŠKA: TRANSMITER OFFLINE]"
    except Exception as e:
        logging.error(f"Nepredviđena greška u generisanju AI: {e}")
        return "[GREŠKA: NEPOZNATA GREŠKA U AI MODULU]"


def get_epilogue_message(epilogue_type):
    """Vraća poruku za specifičan kraj igre."""
    if epilogue_type == "END_SHARE":
        return generate_final_secret() + "\n\n[UPOZORENJE: SIGNAL PREKINUT. NADGLEDANJE AKTIVIRANO.]"
    elif epilogue_type == "END_SAVE":
        return "Primio sam tvoju odluku. Fajl je kriptovan. Čekaj dalje instrukcije.\n\n[SIGNAL IZGUBLJEN]"
    elif epilogue_type == "END_DESTROY":
        return "Razumem. Možda si upravo spasio svet… ili ga osudio.\n\n[TRANSMITER UGAŠEN]"
    elif epilogue_type == "END_WAIT":
        return "Razumem. Čekanje je mudrost. Ali vreme ističe. Javi se kad budeš spreman.\n\n[KRAJ SIGNALA]"
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
            
            is_existing_player = (player is not None)
            
            if player:
                # Resetovanje stanja za novu igru
                player.current_riddle = "START" # Postavlja na početnu fazu
                player.solved_count = 0 
                player.score = 0 
                player.general_conversation_count = 0 
                
            else:
                user = message.from_user
                display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
                
                player = PlayerState(
                    chat_id=chat_id, current_riddle="START", solved_count=0, score=0, 
                    is_disqualified=False, username=display_name, general_conversation_count=0
                )
                session.add(player)
            
            session.commit()
            
            start_message = GAME_STAGES["START"]["text"]
            send_msg(message, start_message)

        elif message.text.lower() == '/stop' or message.text.lower() == 'stop':
            if player and player.current_riddle:
                player.current_riddle = None 
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
        for response_keyword, next_key in current_stage["responses"].items():
            if response_keyword in korisnikov_tekst:
                next_stage_key = next_key
                break

        if next_stage_key:
            player.current_riddle = next_stage_key
            session.commit()

            if next_stage_key.startswith("END_"):
                # Kraj igre, prikaži epilog
                epilogue_message = get_epilogue_message(next_stage_key)
                send_msg(message, epilogue_message)
            else:
                # Pređi na sledeću fazu
                next_stage_data = GAME_STAGES.get(next_stage_key)
                if next_stage_data:
                    send_msg(message, next_stage_data["text"])
        else:
            # Pogrešan odgovor
            send_msg(message, "Signal slabi... Odgovor nije prepoznat. Pokušaj ponovo.")

    finally:
        session.close()

""" Stari handler za opštu konverzaciju je uklonjen jer nova logika igre zahteva striktne odgovore.
        # HANDLER 2: OPŠTA KONVERZACIJA (VAN ZAGONETKE)
        else:
            MAX_CONVERSATION_COUNT = 5
            
            is_conversation_request = (trenutna_zagonetka is None or trenutna_zagonetka in ["FINAL_WARNING_QUERY", "RETURN_CONFIRMATION_QUERY"] or player.is_disqualified)

            if is_conversation_request:
                
                if player.general_conversation_count >= MAX_CONVERSATION_COUNT:
                    send_msg(message, "Vreme je vrednost koju ne smeš rasipati. Tvoja volja je krhka, a tišina te čeka. Moram da znam, Prijatelju: **Da li želiš da nastaviš ili odustaješ?** Odgovori isključivo **DA** ili **NE**.")
                    player.current_riddle = "FINAL_WARNING_QUERY"
                    session.commit()
                    return

                def generate_conversation_response(user_query, player_state):
                    # KORIGOVAN PROMPT (V3.82): Dozvoljava fleksibilan poetski odgovor (DVE rečenice).
                    prompt_base = (
                        f"Putnik ti je postavio pitanje/komentar ('{user_query}'). Trenutno nije usred zagonetke. "
                        "Odgovori mu poetskim, V-stila tekstom (strogo DVE rečenice). **Ne pominji nikakvu komandu.**"
                    )
                    return generate_ai_response(prompt_base, player_state)

                ai_odgovor_base = generate_conversation_response(korisnikov_tekst, player)
                
                if player.is_disqualified:
                    ai_odgovor = DISQUALIFIED_MESSAGE + " Ako zaista nosiš **Volju** da se vratiš Teretu, kucaj **/start** ponovo, Prijatelju."
                else:
                    ai_odgovor = ai_odgovor_base + "\n\n**Samo Volja stvara Put. Odmah kucaj /pokreni ili /zagonetka** da nastaviš Teret."

                send_msg(message, ai_odgovor)
                
                player.general_conversation_count += 1
                session.commit()
                return
            
            else:
                 # Uhvatiće sve ostale slučajeve gde korisnik priča, a treba da odgovori
                 prompt = (
                    f"Putnik je, umesto odgovora na zagonetku, napisao: '{korisnikov_tekst}'. Zagonetka glasi: '{trenutna_zagonetka}'. "
                    "Generiši kratak, poetski, V-stila odgovor koji ga podseća da se fokusira na zagonetku."
                 )
                 ai_reminder = generate_ai_response(prompt, player)
                 send_msg(message, ai_reminder)
                 return

    finally:
        session.close()

# ----------------------------------------------------
# 8. POKRETANJE APLIKACIJE (V3.92)
# ----------------------------------------------------
# Aplikacija se pokreće preko Procfile/Gunicorn-a. 
# Ovo osigurava stabilnost na Renderu.
"""
