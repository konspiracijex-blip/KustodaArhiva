import flask
import telebot
import os
import logging

# UČITAVA TOKEN IZ RENDER OKRUŽENJA!
# Token ne sme biti ovde hardkodiran.
BOT_TOKEN = os.environ.get('BOT_TOKEN') 
# Uklonjena je provera "if not BOT_TOKEN:", jer je ona uzrokovala Build Failed.
    
# Render automatski generiše URL
WEBHOOK_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://placeholder.com/')

# Inicijalizacija bota i Flask aplikacije
# Ako BOT_TOKEN nije dobar, telebot ce srusiti aplikaciju, ali tek nakon pokretanja
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = flask.Flask(__name__)

# --- WEBHOOK RUTE ---

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

# Osnovna ruta (Ostaje zakomentarisana da eliminiše 404 grešku na '/')
# @app.route('/')
# def index():
#     return "Telegram Bot KustodaArhiva je aktivan. Posetite /set_webhook za aktivaciju."

# --- BOT HANDLERI ---

@bot.message_handler(commands=['start'])
def handle_start(message):
    bot.reply_to(message, "Zdravo! Ja sam KustodaArhiva. Pošaljite mi bilo kakvu poruku!")

@bot.message_handler(func=lambda message: True)
def echo_message(message):
    bot.reply_to(message, "Primio sam: " + message.text)
