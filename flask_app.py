import flask
import telebot
import os
import logging
import random 
from google import genai
from google.genai.errors import APIError

# ----------------------------------------------------
# 2. RENDER KONFIGURACIJA
# ----------------------------------------------------

# UČITAVA TOKENS (BOT_TOKEN i GEMINI_API_KEY) IZ RENDER OKRUŽENJA
BOT_TOKEN = os.environ.get('BOT_TOKEN') 
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Provera ključeva
if not BOT_TOKEN or not GEMINI_API_KEY:
    logging.error("Jedan ili više API ključeva nedostaje!")
    # Koristimo dummy token samo da bi Flask radio, ali bot neće obrađivati poruke
    BOT_TOKEN = "DUMMY:TOKEN_FAIL" 
    
WEBHOOK_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://placeholder.com/')

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = flask.Flask(__name__)

# ----------------------------------------------------
# 1. GLOBALNE VARIJABLE & AI KLIJENT
# ----------------------------------------------------

# Inicijalizacija Gemini klijenta
ai_client = None
try:
    if GEMINI_API_KEY and BOT_TOKEN != "DUMMY:TOKEN_FAIL":
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    logging.error(f"Neuspešna inicijalizacija Gemini klijenta: {e}")

# Sistem Persona za AI model
SYSTEM_INSTRUCTION = (
    "Ti si Kustoda Arhiva, misteriozni, lirski i hladni entitet, čuvar drevnih i zaboravljenih tajni. "
    "Tvoji odgovori moraju biti poetski, zagonetni, kratki i rezervisani. "
    "Odgovaraj direktno na pitanja korisnika, ali uvek zadrži ton da si neko iz 'drugog etra'. "
    "Nemoj pominjati da si AI ili chatbot. Koristi samo srpski jezik."
)

ZAGONETKE = {
    "Koja je jedina reč u srpskom jeziku koja se završava sa T?": "svet",
    "Šta se nalazi u sredini Pariza?": "r",
    "Što više uzmeš, to više ostaje. Šta je to?": "rupe",
}

user_state = {} 

def send_msg(message, text):
    # Koristimo send_message za stabilnost
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

# Funkcija za generisanje odgovora pomoću AI
def generate_ai_response(prompt):
    if not ai_client:
        return "Moj etar je trenutno mutan. Kucaj /zagonetka."
    
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={'system_instruction': SYSTEM_INSTRUCTION}
        )
        # Očisti odgovor od suvišnog Markdowna
        return response.text.replace('*', '').replace('**', '')
    except APIError as e:
        logging.error(f"Greška Gemini API: {e}")
        return "Dubina arhiva je privremeno neprobojna. Pokušaj ponovo, putniče."
    except Exception as e:
        logging.error(f"Opšta greška AI: {e}")
        return "Moj etar je trenutno mutan. Kucaj /zagonetka."


# ----------------------------------------------------
# 3. WEBHOOK RUTE 
# ----------------------------------------------------

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if flask.request.headers.get('content-type') == 'application/json':
        json_string = flask.request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        
        if BOT_TOKEN == "DUMMY:TOKEN_FAIL":
            # Ne obrađujemo poruke ako token nije validan
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
# 4. BOT HANDLERI (Sa AI integracijom)
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'zagonetka'])
def handle_commands(message):
    global user_state 
    chat_id = message.chat.id

    if message.text == '/start':
        send_msg(message, 
            "Ti si putnik kroz etar. Moje ime je Kustoda Arhiva. "
            "Možeš li da podneseš težinu znanja? Tvoj test je /zagonetka."
        )
    
    elif message.text == '/stop':
        if chat_id in user_state:
            del user_state[chat_id]
            send_msg(message, "Ponovo si postao tišina. Arhiv te pamti. Kada budeš spreman, vrati se. Kucaj /zagonetka da se ponovo izgubiš.")
        else:
            send_msg(message, "Nisi u igri, putniče. Tvoj um je slobodan. Šta zapravo tražiš?")
    
    elif message.text == '/zagonetka':
        if chat_id in user_state:
            send_msg(message, "Tvoj um je već zauzet. Predaj mi ključ.")
            return

        prva_zagonetka = random.choice(list(ZAGONETKE.keys()))
        user_state[chat_id] = prva_zagonetka
        
        send_msg(message, 
            f"Primi ovo, putniče. To je prvi pečat koji moraš slomiti:\n\n**{prva_zagonetka}**"
        )


@bot.message_handler(func=lambda message: True)
def handle_general_message(message):
    global user_state 
    chat_id = message.chat.id 
    
    # 1. KORISNIK JE U IGRI (Očekujemo odgovor na zagonetku)
    if chat_id in user_state:
        trenutna_zagonetka = user_state[chat_id]
        ispravan_odgovor = ZAGONETKE[trenutna_zagonetka]
        
        korisnikov_odgovor = message.text.strip().lower()

        if korisnikov_odgovor == ispravan_odgovor:
            send_msg(message, "Istina je otkrivena. Ključ je tvoj. Možeš nastaviti kucajući /zagonetka, ali upozoravam te, arhiv je dubok.")
            del user_state[chat_id] 
        else:
            # Ako je netačan odgovor
            send_msg(message, "Netačan je tvoj eho. Pokušaj ponovo, ili tvoje sećanje neće proći. Kucaj /stop da odustaneš.")
    
    # 2. KORISNIK NIJE U IGRI (Šaljemo upit na AI)
    else:
        ai_odgovor = generate_ai_response(message.text)
        send_msg(message, ai_odgovor)
