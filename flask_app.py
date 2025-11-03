import flask
import telebot
import os
import logging
import random 
from google import genai
from google.genai.errors import APIError

# ----------------------------------------------------
# 2. RENDER KONFIGURACIJA (SIGURNO)
# ----------------------------------------------------

# Čita ključeve iz okruženja (Render Environment)
BOT_TOKEN = os.environ.get('BOT_TOKEN') 
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY') 

if not BOT_TOKEN or not GEMINI_API_KEY:
    logging.error("Jedan ili više API ključeva nedostaje! AI će biti neaktivan.")
    BOT_TOKEN = "DUMMY:TOKEN_FAIL" 
    
# Render URL za Webhook
WEBHOOK_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://placeholder.com/')

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = flask.Flask(__name__)

# ----------------------------------------------------
# 1. GLOBALNE VARIJABLE & AI KLIJENT
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

user_state = {} 

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
    except APIError as e:
        logging.error(f"Greška Gemini API: {e}")
        return "Dubina arhiva je privremeno neprobojna. Pokušaj ponovo, putniče."
    except Exception as e:
        logging.error(f"Opšta greška AI: {e}")
        return "Moj etar je trenutno mutan. Kucaj /zagonetka."


def generate_opening_message():
    if not ai_client:
        return "Ti si putnik kroz etar. Moje ime je Kustoda Arhiva. Tvoj test je /zagonetka."
    
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
# 3. WEBHOOK RUTE 
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
# 4. BOT HANDLERI 
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'zagonetka'])
def handle_commands(message):
    global user_state 
    chat_id = message.chat.id

    if message.text == '/start':
        # UKLANJANJE DUPLIRANJA: Duga poruka (AI) + kratka poruka (hardkodirana)
        uvodna_poruka = generate_opening_message()
        send_msg(message, uvodna_poruka)
        # Smatramo da AI poziva na /zagonetka. Da osiguramo stabilnost:
        send_msg(message, "Kucaj /zagonetka da započneš. Vremena je malo.")
        return # DODAVANJE RETURN DA SPREČI VIŠESTRUKU OBRADU
    
    elif message.text == '/stop':
        if chat_id in user_state:
            del user_state[chat_id]
            send_msg(message, "Ponovo si postao tišina. Arhiv te pamti. Nisi uspeo da poneseš teret znanja. Kada budeš spreman, vrati se kucajući /zagonetka.")
        else:
            send_msg(message, "Nisi u testu, Putniče. Šta zapravo tražiš?")
    
    elif message.text == '/zagonetka':
        if chat_id in user_state:
            send_msg(message, "Tvoj um je već zauzet. Predaj mi ključ.")
            return

        prva_zagonetka = random.choice(list(ZAGONETKE.keys()))
        user_state[chat_id] = prva_zagonetka
        
        send_msg(message, 
            f"Primi ovo, Putniče. To je prvi pečat koji moraš slomiti, prvi test tvoje posvećenosti:\n\n**{prva_zagonetka}**"
        )


@bot.message_handler(func=lambda message: True)
def handle_general_message(message):
    global user_state 
    chat_id = message.chat.id 
    korisnikov_tekst = message.text.strip().lower()

    # 1. KORISNIK JE U IGRI (Očekujemo odgovor na zagonetku)
    if chat_id in user_state:
        trenutna_zagonetka = user_state[chat_id]
        ispravan_odgovor = ZAGONETKE[trenutna_zagonetka]
        
        # PROVERA 1: Pomoć / Savet ILI spominjanje imena Dimitrija
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
            send_msg(message, "Istina je otkrivena. Ključ je tvoj. Tvoja posvećenost je dokazana. Spremi se za sledeći test kucajući /zagonetka.")
            del user_state[chat_id] 
        else:
            send_msg(message, "Netačan je tvoj eho. Tvoje sećanje je slabo. Pokušaj ponovo, ili kucaj /stop da odustaneš od Tajne.")
    
    # 2. KORISNIK NIJE U IGRI (Šaljemo upit na AI - Kustoda aktivno vodi)
    else:
        ai_odgovor = generate_ai_response(message.text)
        send_msg(message, ai_odgovor)
