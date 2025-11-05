# app.py
import os
import random
from flask import Flask, request
import telebot

# =====================================
# TELEGRAM BOT TOKEN
# =====================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("Error: TELEGRAM_TOKEN environment variable must be set")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# =====================================
# LIČNOST DIMITRIJA
# =====================================
DIMITRI_PERSONALITY = {
    "ime": "Dimitrije",
    "stil": "misteriozan, sarkastičan, inteligentan, pomalo distopijski",
    "recnik": [
        "vidiš", "shvatićeš", "nisam siguran da bi iko drugi...", "ali ovo je..."
    ],
    "ton": "kratki, direktni odgovori sa primesama ironije i filozofskog promišljanja"
}

def generate_response(user_input):
    """
    Generiše odgovor simulirajući ličnost Dimitrija
    """
    recnik = DIMITRI_PERSONALITY["recnik"]
    
    odgovori = [
        f"{random.choice(recnik)} {user_input}... ali moraš razmišljati dalje.",
        f"{user_input}? {random.choice(recnik)}",
        f"Vidi, {user_input}, ali stvarnost je uvek komplikovanija.",
        f"Da, {user_input}, ali samo onaj ko razume ... shvatiće pravu dimenziju."
    ]
    
    return random.choice(odgovori)

# =====================================
# TELEGRAM HANDLER
# =====================================
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    odgovor = generate_response(user_text)
    bot.reply_to(message, odgovor)

# =====================================
# FLASK SERVER ZA WEBHOOK
# =====================================
app = Flask(__name__)

@app.route("/" + TELEGRAM_TOKEN, methods=["POST"])
def webhook():
    json_str = request.get_data().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route("/")
def index():
    return "Bot radi. Endpoint za webhook je /{}".format(TELEGRAM_TOKEN)

# =====================================
# POSTAVLJANJE WEBHOOK-A (MOŽE SE POŽELJETI SAMO PRVI PUT)
# =====================================
def set_webhook(url):
    """
    Postavlja webhook na dati URL
    """
    webhook_url = f"{url}/{TELEGRAM_TOKEN}"
    success = bot.set_webhook(url=webhook_url)
    if success:
        print(f"Webhook postavljen: {webhook_url}")
    else:
        print("Greška pri postavljanju webhooka")

# =====================================
# START SERVER
# =====================================
if __name__ == "__main__":
    # Ako želiš automatski postaviti webhook, otkomentariši:
    # set_webhook("https://tvoj-domen.com")
    
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
