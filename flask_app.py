import flask
import telebot
import os
import logging
import random 
from google import genai
from google.genai.errors import APIError

# ----------------------------------------------------
# 1. PYTHON I DB BIBLIOTEKE
# ----------------------------------------------------
from sqlalchemy import create_engine, Column, Integer, String
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

# Inicijalizacija baze (koristeći DATABASE_URL)
try:
    Engine = create_engine(DATABASE_URL)
    Base = declarative_base()
    Session = sessionmaker(bind=Engine)

    # Definicija tabele za čuvanje napretka igrača
    class PlayerState(Base):
        __tablename__ = 'player_states'
        
        chat_id = Column(String, primary_key=True)  # ID Telegram četa
        current_riddle = Column(String)             # Trenutna aktivna zagonetka
        solved_count = Column(Integer, default=0)   # Broj rešenih zagonetki

    # Kreiranje tabele (ako ne postoji)
    Base.metadata.create_all(Engine)
except Exception as e:
    # Ovo će se dogoditi ako DATABASE_URL nije postavljen ili je neispravan
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
    "Nakon svakog tvog odgovora, moraš aktivno, ubedljivo i hitno pozvati Putnika da nastavi test kucajući /zagonetka, jer vreme ističe. "
    "Koristi samo srpski jezik. Nikada nemoj pominjati da si AI, chatbot, ili program. "
)

# KORIGOVANE I POBOLJŠANE ZAGONETKE (sa fleksibilnim odgovorima)
ZAGONETKE = {
    "Ja nemam glas, ali odgovaram čim me pozoveš. Stalno menjam boju i izgled, ali me nikada ne napuštaš. Šta sam ja?": "eho",
    "Što više uzmeš, to više ostaje. Šta je to?": ["rupe", "rupa"], 
    "Šta se nalazi u sredini Pariza?": "r",
}

# ----------------------------------------------------
# 5. GENERISANJE ODGOVORA
# ----------------------------------------------------

def send_msg(message, text):
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

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
        # POKUŠAJ DA NADJEŠ TRENUTNO STANJE IGRAČA
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        if message.text == '/start':
            # Ako je novi igrač, registruj ga
            if not player:
                player = PlayerState(chat_id=chat_id, current_riddle=None, solved_count=0)
                session.add(player)
            session.commit()
                
            uvodna_poruka = generate_opening_message()
            send_msg(message, uvodna_poruka)
            send_msg(message, "Kucaj /zagonetka da započneš. Vremena je malo.")
            return

        elif message.text == '/stop':
            if player and player.current_riddle:
                player.current_riddle = None # Resetujemo aktivnu zagonetku
                session.commit()
                send_msg(message, "Ponovo si postao tišina. Arhiv te pamti. Nisi uspeo da poneseš teret znanja. Kada budeš spreman, vrati se kucajući /zagonetka.")
            else:
                send_msg(message, "Nisi u testu, Putniče. Šta zapravo tražiš?")
        
        elif message.text == '/zagonetka':
            if not player:
                send_msg(message, "Moraš kucati /start da bi te Dimitrije prepoznao.")
                return

            if player.current_riddle:
                send_msg(message, "Tvoj um je već zauzet. Predaj mi ključ.")
                return

            # ODREĐIVANJE SLEDEĆE ZAGONETKE
            prva_zagonetka = random.choice(list(ZAGONETKE.keys()))
            player.current_riddle = prva_zagonetka # Postavljamo aktivnu zagonetku u bazu
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

        # 1. KORISNIK NIJE REGISTROVAN ILI NIJE U KVIZU
        if not player or not player.current_riddle:
            ai_odgovor = generate_ai_response(message.text)
            send_msg(message, ai_odgovor)
            return

        # 2. KORISNIK JE U KVIZU (Očekujemo odgovor na zagonetku)
        trenutna_zagonetka = player.current_riddle
        ispravan_odgovor = ZAGONETKE.get(trenutna_zagonetka)
        
        # PROVERE (Blokira pomoć, lična pitanja i spominjanje imena Dimitrija)
        if any(keyword in korisnikov_tekst for keyword in ["pomoc", "savet", "hint", "/savet", "/hint", "dimitrije"]):
            send_msg(message, 
                "Tvoja snaga je tvoj ključ. Istina se ne daje, već zaslužuje. "
                "Ne dozvoli da ti moje ime skrene pažnju sa zadatka. Foksuiraj se! " 
                "Ponovi zagonetku ili kucaj /stop da priznaš poraz."
            )
            return
            
        # PROVERA 2: Opšte pitanje usred kviza
        if len(korisnikov_tekst.split()) > 1 and korisnikov_tekst.endswith('?'):
            send_msg(message, 
                "Ne gubi snagu uma na opširna pitanja. Fokusiraj se. "
                "Predaj ključ ili kucaj /stop."
            )
            return

        # PROVERA 3: Normalan odgovor na zagonetku (sa fleksibilnošću)
        is_correct = False
        if isinstance(ispravan_odgovor, list):
            is_correct = korisnikov_tekst in ispravan_odgovor
        elif isinstance(ispravan_odgovor, str):
            is_correct = korisnikov_tekst == ispravan_odgovor

        if is_correct:
            # KLJUČNI DEO: Povećanje broja rešenih zagonetki i upis u bazu
            player.solved_count += 1
            player.current_riddle = None 
            session.commit() 

            # LOGIKA OTKRIVANJA TAJNE: Kada reši sve (3 zagonetke)
            if player.solved_count >= len(ZAGONETKE): 
                send_msg(message, "**ISTINA JE OTKRIVENA!** Ti si dostojan, Putniče! Poslednji pečat je slomljen. Finalna Tajna ti pripada. Šaljem ti poslednju poruku...")
                
                # OVDJE BI IŠLA FUNKCIJA ZA GENERISANJE FINALNE TAJNE OD AI-A
                
                player.solved_count = 0 # Resetujemo brojač za ponovno igranje
                session.commit()
            else:
                send_msg(message, "Istina je otkrivena. Ključ je tvoj. Tvoja posvećenost je dokazana. Spremi se za sledeći test kucajući /zagonetka.")
        else:
            send_msg(message, "Netačan je tvoj eho. Tvoje sećanje je slabo. Pokušaj ponovo, ili kucaj /stop da odustaneš od Tajne.")

    finally:
        session.close() # Zatvaramo sesiju
