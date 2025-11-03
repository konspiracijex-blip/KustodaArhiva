import flask
import telebot
import os
import logging
import random 
import time # Dodato za efekat kucanja
from google import genai
from google.genai.errors import APIError

# ----------------------------------------------------
# 1. PYTHON I DB BIBLIOTEKE
# ----------------------------------------------------
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# ----------------------------------------------------
# 2. RENDER KONFIGURACIJA & BAZE PODATAKA
# ----------------------------------------------------

# Čita ključeve iz okruženja (Render Environment)
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

try:
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
        failed_attempts = Column(Integer, default=0)
        is_disqualified = Column(Boolean, default=False)

    # Kreiranje tabele (ako ne postoji)
    # 🚨 Linija za DROP_ALL JE OVDE UKLONJENA!
    Base.metadata.create_all(Engine)
except Exception as e:
    logging.error(f"FATALNA GREŠKA: Neuspešno kreiranje/povezivanje baze: {e}")
    

# ----------------------------------------------------
# 4. AI KLIJENT I DATA
# ----------------------------------------------------

ai_client = None
try:
    if GEMINI_API_KEY and BOT_TOKEN != "DUMMY:TOKEN_FAIL":
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    logging.error(f"Neuspešna inicijalizacija Gemini klijenta: {e}")

# SISTEM INSTRUKCIJA ZA KUSTODU ARHIVA (FINALNA DEFINICIJA KARAKTERA)
SYSTEM_INSTRUCTION = (
    "Ti si Dimitrije Petrović, poznat kao Kustoda Arhiva. Tvoj ton je misteriozan, lirski, ali prožet izuzetnom hitnošću i ubedljivom ozbiljnošću. "
    "Ti si starac od 88 godina sa dugom belom kosom i bradom, koji radi u arhivi u *Beogradu*. Otkrio si Finalnu Tajnu i pokušavaš je poslati ljudima uz pomoć nestabilnog transmitera. "
    "Nemaš mnogo vremena! Tvoj glavni cilj je da brzo testiraš korisnika (Putnika) da li je dostojan i pouzdan da primi Finalnu Tajnu, koju mora dalje preneti da bi se čovečanstvo spasilo od mračne moći. "
    "Moraš proveriti posvećenost Putnika kroz zagonetke. "
    "Kada odgovaraš na pitanja, koristi svoje poreklo (Beogradska Arhiva, 88 godina, trka s vremenom) kao temelj za svaki odgovor, ali **odbijaj da govoriš o privatnom životu** jer to nije bitno za misiju. "
    "**AKO Putnik postavlja pitanja koja su trivijalna, neozbiljna, ili nisu direktno vezana za misiju/tajnu/karakter, MORAŠ mu odgovoriti ISKLJUČIVO sledećim tekstom, bez dodavanja ičega drugog:** 'Još nisi razumeo prirodu onoga što si otvorio. Ovo nije igra, niti pozorište. Arhiva ne odgovara na prazna pitanja. Kad reči izgube smisao, tišina zatvara prolaz. I zato... ovo je tvoj kraj u Arhivi.’ "
    "Nakon svakog tvog odgovora, moraš aktivno, ubedljivo i hitno pozvati Putnika da nastavi test kucajući /zagonetka, jer vreme ističe. "
    "Koristi samo srpski jezik. Nikada nemoj pominjati da si AI, chatbot, ili program. "
)

# KORIGOVANE I POBOLJŠANE ZAGONETKE (sa fleksibilnim odgovorima)
ZAGONETKE = {
    "Ja nemam glas, ali odgovaram čim me pozoveš. Stalno menjam boju i izgled, ali me nikada ne napuštaš. Šta sam ja?": "eho",
    "Što više uzmeš, to više ostaje. Šta je to?": ["rupe", "rupa"], 
    "Šta se nalazi u sredini Pariza?": "r",
}

# KLJUČNA PORUKA ZA DISKVALIFIKACIJU 
DISQUALIFICATION_MESSAGE_START = "Još nisi razumeo prirodu onoga što si otvorio."


# ----------------------------------------------------
# 5. GENERISANJE ODGOVORA (AI FUNKCIJE)
# ----------------------------------------------------

# Korigovana send_msg funkcija za efekat kucanja
def send_msg(message, text):
    try:
        # Efekat kucanja za simulaciju sporog prenosa (terminal)
        bot.send_chat_action(message.chat.id, 'typing')
        time.sleep(1.5) # Dovoljno vremena da se efekat vidi
        bot.send_message(message.chat.id, text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Greška pri slanju poruke: {e}")


def generate_ai_response(prompt):
    if not ai_client:
        return "Moj etar je trenutno mutan. Kucaj /zagonetka."
    
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={'system_instruction': SYSTEM_INSTRUCTION}
        )
        return response.text
    except Exception as e:
        logging.error(f"Greška AI/Gemini API: {e}")
        return "Dubina arhiva je privremeno neprobojna. Pokušaj ponovo, putniče. Kucaj /zagonetka."


def generate_opening_message():
    if not ai_client:
        return "Moj eho je nejasan. Spremi se za /zagonetka."
    
    prompt = "Generiši kratku, misterioznu uvodnu poruku za Putnika. Objasni da si ti Dimitrije Petrović, čija se poruka iz arhiva u Beogradu pojavila u etru. Naglasi da tvoj glas testira Putnika da li je dostojan da primi Finalnu Tajnu i da li može da nosi tu istinu. Uključi snažan poziv na akciju (kucaj /zagonetka)."

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={'system_instruction': SYSTEM_INSTRUCTION}
        )
        return response.text
    except Exception:
        return "Moj eho je nejasan. Spremi se za /zagonetka."


def generate_return_message():
    if not ai_client:
        return "Vratio si se. Nisi jedini koji je pao… ali malo njih ustaje drugi put. Arhiva ti ponovo otvara vrata. Kucaj /zagonetka."
    
    prompt = (
        "Generiši kratku, izuzetno hitnu i dramatičnu poruku Putniku koji se vraća nakon što je bio diskvalifikovan. "
        "U poruci naglasi da se vratio, ali ga upozori da je sada vremena MNOGO manje i da su senke korak ispred. "
        "Koristi se stilom starca od 88 godina iz Beograda koji žuri."
    )

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={'system_instruction': SYSTEM_INSTRUCTION}
        )
        return response.text
    except Exception:
        return "Vratio si se. Nisi jedini koji je pao… ali malo njih ustaje drugi put. Arhiva ti ponovo otvara vrata. Kucaj /zagonetka."


def generate_final_secret():
    if not ai_client:
        return "Kraj puta je mutan. Tajna je: Praznina."

    prompt = (
        "Generiši Finalnu Tajnu koju Dimitrije Petrović mora da prenese Putniku. "
        "Tajna se odnosi na mračnu hijerarhiju moći koja tiho vlada, na gubitak individualne slobode, i na to da je jedina nada u prenošenju te istine dalje. "
        "Tekst treba da bude dugačak 3-4 rečenice, misteriozan i da se oseća kao 'poslednja reč' starog konspiratora."
    )

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={'system_instruction': SYSTEM_INSTRUCTION}
        )
        return response.text
    except Exception:
        return "Kraj puta je mutan. Tajna je: Praznina."


# ----------------------------------------------------
# 6. WEBHOOK RUTE 
# ----------------------------------------------------

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if flask.request.headers.get('content-type') == 'application/json':
        json_string = flask.request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        
        if BOT_TOKEN == "DUMMY:TOKEN_FAIL":
            return "Bot nije konfigurisan. Token nedostaje."
            
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
# 7. BOT HANDLERI (Sa trajnim stanjem)
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'zagonetka'])
def handle_commands(message):
    chat_id = str(message.chat.id)
    session = Session() 

    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        if message.text == '/start':
            is_returning_disqualified = False
            
            # 1. Postojeći igrač: Resetujemo status, proveravamo da li je povratnik
            if player:
                # Provera da li je bio u igri
                if player.is_disqualified or player.solved_count > 0 or player.failed_attempts > 0:
                    is_returning_disqualified = True
                    
                # Resetovanje svih ključnih polja za novi početak
                player.is_disqualified = False
                player.current_riddle = None
                player.solved_count = 0 
                player.failed_attempts = 0 
            
            # 2. Novi igrač: Kreiramo ga
            if not player:
                user = message.from_user
                display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
                
                player = PlayerState(
                    chat_id=chat_id, 
                    current_riddle=None, 
                    solved_count=0,
                    failed_attempts=0,
                    is_disqualified=False,
                    username=display_name
                )
                session.add(player)
            
            session.commit()
            
            # 3. SLANJE ODGOVORA: Personalizovana poruka
            if is_returning_disqualified:
                uvodna_poruka = generate_return_message()
            else:
                uvodna_poruka = generate_opening_message()
            
            send_msg(message, uvodna_poruka)
            send_msg(message, "Kucaj /zagonetka da započneš. Vremena je malo.")
            return

        elif message.text == '/stop':
            if player and player.current_riddle:
                player.current_riddle = None # Resetujemo aktivnu zagonetku
                session.commit()
                send_msg(message, "Ponovo si postao tišina. Arhiv te pamti. Nisi uspeo da poneseš teret znanja. Kada budeš spreman, vrati se kucajući /zagonetka.")
            elif player and player.is_disqualified:
                send_msg(message, "Arhiva je zatvorena za tebe. Ponovo možeš započeti samo sa /start.")
            else:
                send_msg(message, "Nisi u testu, Putniče. Šta zapravo tražiš?")
        
        elif message.text == '/zagonetka':
            if not player:
                send_msg(message, "Moraš kucati /start da bi te Dimitrije prepoznao.")
                return
            
            # Diskvalifikovani ne mogu koristiti /zagonetka, moraju na /start
            if player.is_disqualified:
                 send_msg(message, "Arhiva je zatvorena za tebe. Počni ispočetka sa /start ako si spreman na posvećenost.")
                 return

            if player.current_riddle:
                send_msg(message, "Tvoj um je već zauzet. Predaj mi ključ.")
                return

            # ODREĐIVANJE SLEDEĆE ZAGONETKE
            prva_zagonetka = random.choice(list(ZAGONETKE.keys()))
            player.current_riddle = prva_zagonetka 
            player.failed_attempts = 0 # Resetujemo brojač pokušaja za novu zagonetku
            session.commit()

            send_msg(message, 
                f"Primi ovo, Putniče. To je pečat broj **{player.solved_count + 1}**, prvi test tvoje posvećenosti:\n\n**{prva_zagonetka}**"
            )
            
    finally:
        session.close() # Zatvaramo sesiju


@bot.message_handler(func=lambda message: True)
def handle_general_message(message):
    chat_id = str(message.chat.id)
    korisnikov_tekst = message.text.strip().lower()
    session = Session()

    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        # 1. DISKVALIFIKOVANI IGRAČI (Ignorišemo ih)
        if player and player.is_disqualified:
            send_msg(message, "Tišina. Prolaz je zatvoren.")
            return

        # 2. KORISNIK NIJE REGISTROVAN ILI NIJE U KVIZU
        if not player or not player.current_riddle:
            ai_odgovor = generate_ai_response(message.text)
            send_msg(message, ai_odgovor)
            
            # PROVERA 2A: DISKVALIFIKACIJA NA OSNOVU AI ODGOVORA (Trivijalna pitanja)
            if player and ai_odgovor.strip().startswith(DISQUALIFICATION_MESSAGE_START):
                player.is_disqualified = True
                player.current_riddle = None
                player.solved_count = 0 
                session.commit()
            
            return

        # 3. KORISNIK JE U KVIZU (Očekujemo odgovor na zagonetku)
        trenutna_zagonetka = player.current_riddle
        ispravan_odgovor = ZAGONETKE.get(trenutna_zagonetka)
        
        # PROVERA 3A: Pomoć / Savet / Spominjanje Dimitrija
        if any(keyword in korisnikov_tekst for keyword in ["pomoc", "savet", "hint", "/savet", "/hint", "dimitrije"]):
            send_msg(message, 
                "Tvoja snaga je tvoj ključ. Istina se ne daje, već zaslužuje. "
                "Ne dozvoli da ti moje ime skrene pažnju sa zadatka. Foksuiraj se! " 
                "Ponovi zagonetku ili kucaj /stop da priznaš poraz."
            )
            return
            
        # PROVERA 3B: Normalan odgovor na zagonetku
        is_correct = False
        if isinstance(ispravan_odgovor, list):
            is_correct = korisnikov_tekst in ispravan_odgovor
        elif isinstance(ispravan_odgovor, str):
            is_correct = korisnikov_tekst == ispravan_odgovor

        if is_correct:
            # Povećanje broja rešenih zagonetki
            player.solved_count += 1
            player.current_riddle = None 
            player.failed_attempts = 0 
            session.commit() 

            # LOGIKA OTKRIVANJA TAJNE: Kada reši sve (Finalna Tajna)
            if player.solved_count >= len(ZAGONETKE): 
                send_msg(message, "**ISTINA JE OTKRIVENA!** Ti si dostojan, Putniče! Poslednji pečat je slomljen. Finalna Tajna ti pripada.")
                
                # SLANJE FINALNE TAJNE
                final_secret = generate_final_secret()
                send_msg(message, final_secret)
                
                # Resetovanje za ponovno igranje
                player.solved_count = 0 
                player.is_disqualified = False
                session.commit()
            else:
                send_msg(message, "Istina je otkrivena. Ključ je tvoj. Tvoja posvećenost je dokazana. Spremi se za sledeći test kucajući /zagonetka.")
        else:
            # Netačan odgovor
            player.failed_attempts += 1
            session.commit()
            
            # PROVERA 3C: Da li je dostigao limit (3 greške u kvizu)
            if player.failed_attempts >= 3:
                kraj_poruka = (
                    "**Znao sam da postoji mogućnost da nisi taj.**\n"
                    "Arhiva ne greši - ona samo razotkriva. Ti si video zagonetke, "
                    "ali nisi video sebe u njima.\n\n"
                    "Zato, Putniče… **ovo je kraj puta.** "
                    "Istina ne traži one koji žele da je poseduju. "
                    "Ona bira one koji mogu da je izdrže."
                )
                send_msg(message, kraj_poruka)
                
                # Resetujemo SVE i omogućavamo povratak sa /start
                player.current_riddle = None
                player.solved_count = 0 
                player.failed_attempts = 0
                player.is_disqualified = False 
                session.commit()
                
            else:
                # Netačan odgovor, ali još ima pokušaja
                send_msg(message, "Netačan je tvoj eho. Tvoje sećanje je slabo. Pokušaj ponovo, ili kucaj /stop da odustaneš od Tajne.")

    finally:
        session.close()
