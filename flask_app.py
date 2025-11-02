import flask
import telebot
import os
import logging
import random # Dodajemo random modul za nasumičan izbor zagonetki

# ----------------------------------------------------
# 1. GLOBALNE VARIJABLE (STANJE IGRE I ZAGONETKE)
# ----------------------------------------------------

# BAZA ZAGONETKI (Ključ: Pitanje, Vrednost: Tačan odgovor, sve malim slovima)
ZAGONETKE = {
    "Koja je jedina reč u srpskom jeziku koja se završava sa T?": "svet",
    "Šta se nalazi u sredini Pariza?": "r",
    "Što više uzmeš, to više ostaje. Šta je to?": "rupe",
}

# STANJE IGRE KORISNIKA (Ključ: ID korisnika, Vrednost: Ključ trenutne zagonetke)
user_state = {} 

# ----------------------------------------------------
# 2. RENDER KONFIGURACIJA
# ----------------------------------------------------

# UČITAVA TOKEN IZ RENDER OKRUŽENJA (Environment Group)
BOT_TOKEN = os.environ.get('BOT_TOKEN') 
# Render automatski generiše URL
WEBHOOK_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://placeholder.com/')

# Inicijalizacija bota i Flask aplikacije
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = flask.Flask(__name__)

# ----------------------------------------------------
# 3. WEBHOOK RUTE (Za komunikaciju sa Renderom)
# ----------------------------------------------------

# Glavna Webhook ruta (Prima poruke od Telegrama)
@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if flask.request.headers.get('content-type') == 'application/json':
        json_string = flask.request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    else:
        flask.abort(403)

# Ruta za postavljanje Webhooka (Aktivira vezu sa Telegramom)
@app.route('/set_webhook', methods=['GET'])
def set_webhook_route():
    webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
    s = bot.set_webhook(url=webhook_url_with_token)
    
    if s:
        return "Webhook successfully set! Bot je spreman za rad. Pošaljite /start!"
    else:
        try:
            bot.remove_webhook()
            s = bot.set_webhook(url=webhook_url_with_token)
            if s:
                return "Webhook successfully RESET! Bot je spreman!"
            else:
                return "Failed to set webhook. Proverite Render logove."
        except Exception as e:
            return f"Failed to set webhook: {e}"

# Osnovna ruta (Ostaje zakomentarisana da se izbegnu 404 greške na Renderu)
# @app.route('/')
# def index():
#     return "Telegram Bot KustodaArhiva je aktivan. Posetite /set_webhook za aktivaciju."

# ----------------------------------------------------
# 4. BOT HANDLERI (Logika igre i komunikacija)
# ----------------------------------------------------

@bot.message_handler(commands=['start'])
def handle_start(message):
    bot.reply_to(message, 
        "Ti si putnik kroz etar. Moje ime je Kustoda Arhiva. "
        "Možeš li da podneseš težinu znanja? Tvoj test je /zagonetka."
    )

@bot.message_handler(commands=['zagonetka'])
def handle_zagonetka(message):
    user_id = message.chat.id
    
    # 1. Proverava da li je korisnik već u igri
    if user_id in user_state:
        bot.reply_to(message, "Tvoj um je već zauzet. Predaj mi ključ pre nego što kreneš dalje. Odgovori na prethodni upit.")
        return

    # 2. Bira nasumičnu zagonetku
    prva_zagonetka = random.choice(list(ZAGONETKE.keys()))
    
    # 3. Pamti stanje
    user_state[user_id] = prva_zagonetka
    
    # 4. Šalje pitanje (Misteriozni ton)
    bot.reply_to(message, 
        f"Primi ovo, putniče. To je prvi pečat koji moraš slomiti:\n\n**{prva_zagonetka}**"
    )

@bot.message_handler(func=lambda message: True)
def handle_game_answer(message):
    user_id = message.chat.id # Koristimo ispravan user_id
    
    # Proverava da li je korisnik u igri
    if user_id in user_state:
        trenutna_zagonetka = user_state[user_id] # ISPRAVLJEN RED: koristi user_id
        ispravan_odgovor = ZAGONETKE[trenutna_zagonetka]
        
        # Formatiranje odgovora: uklanjanje razmaka i prebacivanje u mala slova
        korisnikov_odgovor = message.text.strip().lower()

        if korisnikov_odgovor == ispravan_odgovor:
            bot.reply_to(message, 
                "Istina je otkrivena. Ključ je tvoj. "
                "Možeš nastaviti kucajući /zagonetka, ali upozoravam te, arhiv je dubok."
            )
            del user_state[user_id] # Briše stanje
        else:
            bot.reply_to(message, 
                "Netačan je tvoj eho. Pokušaj ponovo, ili tvoje sećanje neće proći. "
                "Kucaj /stop da se vratiš u tišinu."
            )
    else:
        # Ponavlja poruku ako korisnik nije u igri
        bot.reply_to(message, 
            "Govoriš u prazno. Kucaj /zagonetka ako želiš da budeš testiran."
        )
