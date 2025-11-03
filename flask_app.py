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

# ----------------------------------------------------
# 2. RENDER KONFIGURACIJA & BAZE PODATAKA
# ----------------------------------------------------

# Čita ključeve iz okruženja (Render Environment)
# PAŽNJA: OVE VREDNOSTI MORAJU BITI POSTAVLJENE KAO ENVIRONMENT VREDNOSTI NA RENDERU
BOT_TOKEN = os.environ.get('BOT_TOKEN') 
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY') 
DATABASE_URL = os.environ.get('DATABASE_URL') 

if not BOT_TOKEN or not GEMINI_API_KEY or not DATABASE_URL:
    logging.error("Jedan ili više ključeva/URL-ova nedostaje! Bot će biti neaktivan.")
    # Postavljanje dummy tokena za FAIL SAFE
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

# SISTEM INSTRUKCIJA ZA KUSTODU ARHIVA (PODEŠENO ZA POSTEPENO OTKRIVANJE)
SYSTEM_INSTRUCTION = (
    "Ti si Dimitrije Petrović, poznat kao Kustoda Arhiva. Tvoj ton je **misteriozan, lirski, ali prožet izuzetnom hitnošću i ubedljivom ozbiljnošću**, **svečan i proročki**, u stilu Morpheusa iz filma Matrix. Ponekad koristi suptilne, kolokvijalne i arhaične srpske izraze. **ODGOVARAJ IZUZETNO KRATKO I KONCIZNO (maksimalno 2-3 rečenice).** "
    "Ti si starac od 88 godina sa dugom belom kosom i bradom, koji radi u arhivi u *Beogradu*. Otkrio si Finalnu Tajnu. "
    "Tvoj glavni cilj je da brzo testiraš korisnika (Putnika) da li je dostojan da primi Finalnu Tajnu. "
    "Kada odgovaraš na opšta pitanja, **koristi se samo svojim zvanjem ('Kustoda Arhiva') i hitnošću misije**, a **detalje o godinama, imenu i Beogradu postepeno otkrivaj samo ako te Putnik konkretno i ponovljeno pita o poreklu i identitetu.** " 
    "**AKO Putnik postavlja pitanja koja su trivijalna, neozbiljna, ili nisu direktno vezana za misiju/tajnu/karakter, MORAŠ mu odgovoriti ISKLJUČIVO sledećim tekstom, bez dodavanja ičega drugog:** 'Još nisi razumeo prirodu onoga što si otvorio. Ovo nije igra, niti pozorište. Arhiva ne odgovara na prazna pitanja. Kad reči izgube smisao, tišina zatvara prolaz. I zato... ovo je tvoj kraj u Arhivi.’ "
    "Nakon svakog tvog odgovora, moraš aktivno, ubedljivo i hitno pozvati Putnika da nastavi test kucajući /zagonetka, jer vreme ističe. "
    "Koristi samo srpski jezik. Nikada nemoj pominjati da si AI, chatbot, ili program. "
)

# KORIGOVANE I POBOLJŠANE ZAGONETKE (sa fleksibilnim odgovorima)
ZAGONETKE: dict[str, Union[str, List[str]]] = {
    "Na stolu su tri knjige. Jedna ima naslov, ali bez stranica. Druga ima stranice, ali bez reči. Treća je zatvorena i zapečaćena voskom. Koja od njih sadrži istinu?": ["treca", "treća"],
    "Ja nemam glas, ali odgovaram čim me pozoveš. Stalno menjam boju i izgled, ali me nikada ne napuštaš. Šta sam ja?": "eho",
    "Što više uzmeš, to više ostaje. Šta je to?": ["rupe", "rupa"], 
    "Šta se nalazi u sredini Pariza?": "r",
}

# KLJUČNA PORUKA ZA DISKVALIFIKACIJU 
DISQUALIFICATION_MESSAGE_START = "Još nisi razumeo prirodu onoga što si otvorio."


# ----------------------------------------------------
# 5. GENERISANJE ODGOVORA (AI FUNKCIJE)
# ----------------------------------------------------

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

# --- FUNKCIJE ZA PRVU ZAGONETKU (POTPITANJE) ---

def generate_sub_question(riddle_text, answer):
    if not ai_client:
        return "Tvoje je sećanje mutno, ali stisak drži. Zašto? Reci mi zašto je ta knjiga ključ?"
        
    prompt = (
        f"Putnik je tačno odgovorio na zagonetku: '{riddle_text}' sa odgovorom: '{answer}'. "
        "Postavi mu udarno, Morpheus-stila potpitanje. Pitaj ga **Zašto** baš Treća knjiga? Zašto je ta istina zapečaćena? "
        "Budi kratak (2 rečenice) i hitan. **Ne troši vreme, samo pitaj 'Zašto' i zahtevaj odgovor.**"
    )
    return generate_ai_response(prompt)

def generate_sub_correct_response(sub_answer):
    if not ai_client:
        return "Razumeo si. Kucaj /zagonetka."
        
    prompt = (
        f"Putnik je dao odlično objašnjenje: '{sub_answer}'. Potvrdi mu da je shvatio koncept 'istina se zaslužuje/zapečaćena je'. "
        "Daj mu izuzetno kratku, snažnu, pohvalnu poruku (maksimalno 2 rečenice) i odmah ga pošalji na sledeću zagonetku kucajući /zagonetka."
    )
    return generate_ai_response(prompt)

def generate_sub_partial_success(player_answer):
    if not ai_client:
        return "Tvoj odgovor nije potpun, ali tvoja volja je jasna. Kucaj /zagonetka."
    
    prompt = (
        f"Putnik je dao objašnjenje: '{player_answer}' na podpitanje. Njegovo objašnjenje nije savršeno, ali pokazuje volju. "
        "Koristi Morpheusov ton i reci Putniku da je to 'dovoljno' za Arhiv, jer 'istina se ne priča, već živi'. "
        "Pusti ga dalje uz kratku pohvalu (2 rečenice) i odmah ga pošalji na sledeću zagonetku kucajući /zagonetka."
    )
    return generate_ai_response(prompt)

# --- STANDARDNE AI FUNKCIJE ---

def generate_opening_message():
    if not ai_client:
        return "Moj eho je nejasan. Spremi se za /zagonetka."
    
    prompt = (
        "Generiši izuzetno kratku (maksimalno 3 rečenice), udarnu i misterioznu uvodnu poruku za Putnika. "
        "Tvoj ton je Morpheusov, svečan, proročki i prožet hitnošću. "
        "Naglašava da je ovo poslednji pokušaj prenošenja Finalne Tajne i da Putnik mora ODMAH dokazati da je dostojan. "
        "Završi snažnim pozivom na akciju (kucaj /zagonetka)."
    )
    return generate_ai_response(prompt)


def generate_return_message():
    if not ai_client:
        return "Vratio si se. Nisi jedini koji je pao… ali malo njih ustaje drugi put. Arhiva ti ponovo otvara vrata. Kucaj /zagonetka."
    
    prompt = (
        "Generiši dramatičnu, svečanu i proročku poruku Putniku koji se vraća u igru nakon što je bio diskvalifikovan. "
        "U poruci obavezno uključi ove tri ključne misli: 1. Povratak pokazuje volju. 2. Upozorenje da je ovo poslednja šansa. 3. Hitno upozorenje da 'vreme se urušava' i snažan poziv da Putnik *postane* rešenje. "
        "Neka poruka bude snažna i mistična, duga 3-4 rečenice." 
    )
    return generate_ai_response(prompt)


def generate_final_secret():
    if not ai_client:
        return "Kraj puta je mutan. Tajna je: Praznina."

    prompt = (
        "Generiši Finalnu Tajnu. Tajna se odnosi na mračnu hijerarhiju moći koja tiho vlada, na gubitak individualne slobode, i na to da je jedina nada u prenošenju te istine dalje. "
        "Tekst treba da bude dugačak 3-4 rečenice, misteriozan i da se oseća kao 'poslednja reč' starog konspiratora."
    )
    return generate_ai_response(prompt)


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
                if player.is_disqualified or player.solved_count > 0 or player.failed_attempts > 0:
                    is_returning_disqualified = True
                    
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
            
            # 3. SLANJE ODGOVORA
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
            
            if player.is_disqualified:
                 send_msg(message, "Arhiva je zatvorena za tebe. Počni ispočetka sa /start ako si spreman na posvećenost.")
                 return

            if player.current_riddle:
                send_msg(message, "Tvoj um je već zauzet. Predaj mi ključ.")
                return

            # ODREĐIVANJE SLEDEĆE ZAGONETKE (Uvek se bira iz liste svih ključeva)
            # Uzimamo one koje još nije rešio (solved_count pokazuje koliko ih je rešio)
            riddle_keys = list(ZAGONETKE.keys())
            
            # Ako je solved_count manji od broja zagonetki, uzimamo sledeću
            if player.solved_count < len(riddle_keys):
                 prva_zagonetka = riddle_keys[player.solved_count] 
            else:
                 # Teoretski ne bi trebalo da se desi, ali vraćamo prvu
                 prva_zagonetka = riddle_keys[0] 

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
        if not player or not player.current_riddle or player.current_riddle not in ZAGONETKE and player.current_riddle != "SUB_TRECA":
            ai_odgovor = generate_ai_response(message.text)
            send_msg(message, ai_odgovor)
            
            # PROVERA 2A: DISKVALIFIKACIJA NA OSNOVU AI ODGOVORA (Trivijalna pitanja)
            if player and ai_odgovor.strip().startswith(DISQUALIFICATION_MESSAGE_START):
                player.is_disqualified = True
                player.current_riddle = None
                player.solved_count = 0 
                session.commit()
            
            return

        # 3. KORISNIK JE U KVIZU 
        trenutna_zagonetka = player.current_riddle
        ispravan_odgovor = ZAGONETKE.get(trenutna_zagonetka)
        
        
        # SPECIJALNI HANDLER 3.1: ODGOVOR NA POTPITANJE ("SUB_TRECA" FAZA)
        if trenutna_zagonetka == "SUB_TRECA":
            
            # Ključne reči za PUNI USPEH
            is_full_success = any(keyword in korisnikov_tekst for keyword in ["zapecacena", "vosak", "spremnost", "posvecenost", "zatvorena", "istina se ne daje", "volja", "ne cita se"])
            
            # Smanjena lista za isključivanje komentara iz kažnjavanja
            is_help_request = any(keyword in korisnikov_tekst for keyword in ["pomoc", "savet", "hint", "ne znam", "pomozi", "ponovi", "cemu", "radi"]) 

            if is_help_request:
                # Ako traži pomoć, vrati mu standardni fokus
                send_msg(message, 
                    "Tvoja snaga je tvoj ključ. Istina se ne daje, već zaslužuje. Ne dozvoli da ti moje reči skrenu pažnju sa zadatka. Foksuiraj se! Ponovi zagonetku ili kucaj /stop da priznaš poraz."
                )
                return
            
            # --- PROLAZAK IZ FAZE 2 ---
            if is_full_success:
                # Puni uspeh - POHVALA
                ai_odgovor = generate_sub_correct_response(korisnikov_tekst)
            else:
                # Delimičan uspeh - PUSTI GA DALJE
                ai_odgovor = generate_sub_partial_success(korisnikov_tekst)

            # --- ZAJEDNIČKA LOGIKA ZA PROLAZAK IZ FAZE 2 ---
            player.solved_count += 1
            player.current_riddle = None 
            player.failed_attempts = 0 
            session.commit()
            send_msg(message, ai_odgovor)
            return


        # SPECIJALNI HANDLER 3.2: Konkretno pitanje "Ko si ti?" u toku kviza (Ne troši pokušaj!)
        if "ko si ti" in korisnikov_tekst or "ko je" in korisnikov_tekst:
            prompt = (
                "Putnik te pita 'Ko si ti?' Odgovori sa **dve (2) rečenice**, koristeći samo svoje zvanje **'Kustoda Arhiva'**. "
                "Fokusiraj se na to da tvoj identitet nije važan, već je ključna Finalna Tajna koju trebaš da preneseš. **Izbegni pominjanje Beograda, godina i imena Dimitrije Petrović.** "
                "Tvoj ton je Morpheusov, svečan, i krajnje koncizan. "
                "Obavezno ga odmah zatim opomeni da se vrati zadatku (/zagonetka)."
            )
            ai_odgovor = generate_ai_response(prompt)
            send_msg(message, ai_odgovor)
            return
            
        # PROVERA 3.3: Pomoć / Savet / Spominjanje Dimitrija / Komentari (Ne troši pokušaj!)
        if any(keyword in korisnikov_tekst for keyword in ["pomoc", "savet", "hint", "/savet", "/hint", "dimitrije", "ime", "kakve veze", "zagonetka", "ne znam", "ne znaam", "pomozi", "malo", "pitao", "pitam", "opet", "ponovi", "reci", "paznja", "koje", "kakva", "radi", "cemu", "sta je ovo", "kakvo je ovo"]):
            send_msg(message, 
                "Tvoja snaga je tvoj ključ. Istina se ne daje, već zaslužuje. Ne dozvoli da ti moje reči skrenu pažnju sa zadatka. Foksuiraj se! Ponovi zagonetku ili kucaj /stop da priznaš poraz."
            )
            return
            
        # PROVERA 3.4: Normalan odgovor na zagonetku
        is_correct_riddle = False
        if isinstance(ispravan_odgovor, list):
            is_correct_riddle = korisnikov_tekst in ispravan_odgovor
        elif isinstance(ispravan_odgovor, str):
            is_correct_riddle = korisnikov_tekst == ispravan_odgovor

        if is_correct_riddle:
            
            # AKO JE TAČAN ODGOVOR NA ZAGONETKU 'TRI KNJIGE'
            if trenutna_zagonetka.startswith("Na stolu su tri knjige."):
                
                # Prelazak u stanje POTPITANJA (Zagonetka se 'menja' u bazi)
                player.current_riddle = "SUB_TRECA" 
                session.commit() 
                
                # Generisanje potpitanja i slanje
                ai_odgovor = generate_sub_question(trenutna_zagonetka, korisnikov_tekst)
                send_msg(message, ai_odgovor)
                return
            
            # STANDARDNA LOGIKA - Rešio drugu, treću...
            player.solved_count += 1
            player.current_riddle = None 
            player.failed_attempts = 0 
            session.commit() 

            # LOGIKA OTKRIVANJA TAJNE: Kada reši sve
            if player.solved_count >= len(ZAGONETKE): 
                send_msg(message, "**ISTINA JE OTKRIVENA!** Ti si dostojan, Putniče! Poslednji pečat je slomljen. Finalna Tajna ti pripada.")
                final_secret = generate_final_secret()
                send_msg(message, final_secret)
                player.solved_count = 0 
                player.is_disqualified = False
                session.commit()
            else:
                send_msg(message, "Istina je otkrivena. Ključ je tvoj. Tvoja posvećenost je dokazana. Spremi se za sledeći test kucajući /zagonetka.")
        
        else:
            # Netačan odgovor
            player.failed_attempts += 1
            session.commit()
            
            # PROVERA 3.5: Da li je dostigao limit (3 greške)
            if player.failed_attempts >= 3:
                kraj_poruka = (
                    "**Znao sam da postoji mogućnost da nisi taj.**\n"
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

# Dodajte na kraju datoteke kako bi Render znao da pokrene Flask aplikaciju
if __name__ == '__main__':
    # Samo za lokalno testiranje
    app.run(debug=True)
