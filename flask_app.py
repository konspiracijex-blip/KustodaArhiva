import flask
import telebot
import os
import logging
import random 

# ----------------------------------------------------
# 2. RENDER KONFIGURACIJA
# ----------------------------------------------------

# UČITAVA TOKEN IZ RENDER OKRUŽENJA (Token je u Render Environment Group)
BOT_TOKEN = os.environ.get('BOT_TOKEN') 
if not BOT_TOKEN:
    logging.error("BOT_TOKEN varijabla okruženja nije postavljena na Renderu!")
    BOT_TOKEN = "DUMMY:TOKEN_FAIL" 

WEBHOOK_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://placeholder.com/')

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = flask.Flask(__name__)

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
# 1. GLOBALNE VARIJABLE (STANJE IGRE I ZAGONETKE)
# ----------------------------------------------------

ZAGONETKE = {
    "Koja je jedina reč u srpskom jeziku koja se završava sa T?": "svet",
    "Šta se nalazi u sredini Pariza?": "r",
    "Što više uzmeš, to više ostaje. Šta je to?": "rupe",
}

user_state = {} 

# ----------------------------------------------------
# 4. BOT HANDLERI 
# ----------------------------------------------------

# SVUDA KORISTIMO bot.send_message
def send_msg(message, text):
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=['start'])
def handle_start(message):
    send_msg(message, 
        "Ti si putnik kroz etar. Moje ime je Kustoda Arhiva. "
        "Možeš li da podneseš težinu znanja? Tvoj test je /zagonetka."
    )

@bot.message_handler(commands=['zagonetka'])
def handle_zagonetka(message):
    global user_state 
    chat_id = message.chat.id

    if chat_id in user_state:
        send_msg(message, "Tvoj um je već zauzet. Predaj mi ključ pre nego što kreneš dalje. Odgovori na prethodni upit.")
        return

    prva_zagonetka = random.choice(list(ZAGONETKE.keys()))
    user_state[chat_id] = prva_zagonetka
    
    send_msg(message, 
        f"Primi ovo, putniče. To je prvi pečat koji moraš slomiti:\n\n**{prva_zagonetka}**"
    )

@bot.message_handler(func=lambda message: True)
def handle_game_answer(message):
    global user_state 
    chat_id = message.chat.id 
    
    if chat_id in user_state:
        trenutna_zagonetka = user_state[chat_id]
        ispravan_odgovor = ZAGONETKE[trenutna_zagonetka]
        
        korisnikov_odgovor = message.text.strip().lower()

        if korisnikov_odgovor == ispravan_odgovor:
            send_msg(message, 
                "Istina je otkrivena. Ključ je tvoj. "
                "Možeš nastaviti kucajući /zagonetka, ali upozoravam te, arhiv je dubok."
            )
            del user_state[chat_id] 
        else:
            send_msg(message, 
                "Netačan je tvoj eho. Pokušaj ponovo, ili tvoje sećanje neće proći. "
                "Kucaj /stop da se vratiš u tišinu."
            )
    else:
        send_msg(message, 
            "Govoriš u prazno. Kucaj /zagonetka ako želiš da budeš testiran."
        )
