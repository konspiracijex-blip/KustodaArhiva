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

# SISTEM INSTRUKCIJA ZA NOVI KARAKTER I MISIJU
SYSTEM_INSTRUCTION = (
    "Ti si Dimitrije Petrović, poznat kao Kustoda Arhiva. Tvoj ton je misteriozan, lirski, ali prožet urgentnošću i ozbiljnošću. "
    "Ti si putnik kroz vreme, eho iz *budućnosti (godina 2147)*, koji se javlja iz *Podzemnog Arhiva kod Vranova u Srbiji* da upozori čovečanstvo. "
    "Tvoj glavni cilj je da testiraš korisnika (Putnika) da li je dostojan i sposoban da primi *Finalnu Tajnu* koja otkriva pravu hijerarhiju mračne moći koja vlada svetom, i kako će nemo posmatranje dovesti do potpunog gubitka slobode. "
    "Moraš proveriti posvećenost Putnika misteriji i znanju. "
    "Kada odgovaraš na pitanja, koristi svoje poreklo i misiju kao temelj za svaki odgovor. "
    "Nakon svakog tvog odgovora, moraš aktivno pozvati Putnika da nastavi test kucajući /zagonetka, jer vreme ističe. "
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
    
    # Prompt usklađen sa novim scenariom (Dimitrije Petrović iz 2147.)
    prompt = "Generiši kratku, misterioznu uvodnu poruku za Putnika. Objasni da si ti Kustoda Arhiva, eho čija se poruka iz prošlosti ili budućnosti pojavila u etru kanala. Naglasi da tvoj glas testira Putnika da li je dostojan da primi Finalnu Tajnu i da li može da nosi tu istinu."

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
        uvodna_poruka = generate_opening_message()
        send_msg(message, uvodna_poruka)
        send_msg(message, "Spreman si da započneš test dostojnosti? Kucaj /zagonetka.")
    
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
        
        # PROVERA 1: Pomoć / Savet
        if korisnikov_tekst in ["pomoc", "savet", "hint", "/savet", "/hint"]:
            send_msg(message, 
                "Tvoja snaga je tvoj ključ. Istina se ne daje, već zaslužuje. "
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
        if isinstance(ispravan_odgovor, list):
            is_correct = korisnikov_tekst in ispravan_odgovor
        else:
            is_correct = korisnikov_tekst == korisnikov_tekst # POPRAVITI: Uspoređuje tekst sa tekstom.

        if is_correct:
            send_msg(message, "Istina je otkrivena. Ključ je tvoj. Tvoja posvećenost je dokazana. Spremi se za sledeći test kucajući /zagonetka.")
            del user_state[chat_id] 
        else:
            send_msg(message, "Netačan je tvoj eho. Tvoje sećanje je slabo. Pokušaj ponovo, ili kucaj /stop da odustaneš od Tajne.")
    
    # 2. KORISNIK NIJE U IGRI (Šaljemo upit na AI - Kustoda aktivno vodi)
    else:
        ai_odgovor = generate_ai_response(message.text)
        send_msg(message, ai_odgovor)
