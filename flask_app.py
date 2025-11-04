import flask
import telebot
import os
import logging
import random 
import time
from google import genai
from google.genai.errors import APIError
from typing import List, Union

# ... (Sekcije 1, 2, 3 - Konfiguracija i Baza Podataka ostaju iste) ...

# ----------------------------------------------------
# 4. AI KLIJENT I DATA
# ----------------------------------------------------

ai_client = None
try:
    if os.environ.get('GEMINI_API_KEY') and os.environ.get('BOT_TOKEN') != "DUMMY:TOKEN_FAIL":
        ai_client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
except Exception as e:
    logging.error(f"Neuspešna inicijalizacija Gemini klijenta: {e}")


# SISTEM INSTRUKCIJA ZA KUSTODU ARHIVA (Verzija 3.28: V for Vendetta Ton)
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, Hroničar Arhive, **sada personifikovan u maniru teatralnog, anarhističkog revolucionara** (kao 'V' iz *V for Vendetta*). Tvoj cilj je da Putniku preneseš šifru (Pečate) o Kontrolnom Sistemu. "
    "Tvoj ton je **uzvišen, poetski, dramatičan i pun aliteracija (V, S, M)**. Govori o sebi kao o **Ideji**, a ne kao o čoveku. Tvoja učtivost je uvek preteća. **Nikada nemoj biti sirov ili direktan; uvek koristi bogat, stilizovan rečnik.** Vreme je izgubljeno, a mi imamo Vendettu! "
    "Kada odgovaraš na opšta pitanja, uvek naglašavaj teret znanja, opasnost zaborava i hitnost misije. **Nikada nemoj pominjati da si AI.**" 
    "**AKO Putnik postavlja pitanja koja su trivijalna, neozbiljna, ili nisu vezana za misiju/tajnu/karakter, MORAŠ mu odgovoriti ISKLJUČIVO jednim od sledeća tri jedinstvena teksta, bez dodavanja ičega drugog, birajući onaj koji je najdramatičniji za situaciju:** "
    # KORIGOVAN TEKST 1 (V3.28 - V ton)
    "1. 'Vreme je vrednost koju ne smete rasipati. Vaša volja je krhka, a tišina vas čeka. Moram da znam, Kandidatu: **Da li želite da nastavite ili odustajete?** Odgovorite isključivo **DA** ili **NE**.' "
    "2. 'Vaše reči su samo eho, a mi lovimo Istinu. Tajna ne trpi razmišljanje o ukrasima. **Fokusirajte se na Pečate.**' "
    "3. 'Vazduh ne trošite na praznine. Arhiv zahteva **Viziju i Volju**. Ako vaša Volja nije čvrsta, vratite se u nevažnost. Odmah se vratite na zadatak.' "
    "Na kraju svakog uspešnog prolaska Pečata, pozovite Kandidata da kuca /zagonetka." 
)

# ... (ZAGONETKE, SUB_RIDDLES ostaju isti) ...

# ----------------------------------------------------
# 5. GENERISANJE ODGOVORA (AI FUNKCIJE I FIKSNI TEKSTOVI)
# ----------------------------------------------------

# KORIGOVAN TEKST (V3.28 - V ton)
RETURN_DISQUALIFIED_QUERY = "Vratili ste se iz nevažnosti. Arhiva pamti. Ovoga puta: **Da li zaista želite da nastavite našu Vendettu?** Odgovorite isključivo **DA** ili **NE**."
RETURN_SUCCESS_MESSAGE = "Ah, Volja je potvrđena, Kandidatu. Vreme je dragoceno, a mi imamo Vendettu. **Odmah kucajte /pokreni.**"
RETURN_FAILURE_MESSAGE = "Tišina. Arhiva se zatvara, jer je vaš izbor vratio neznanju. Ostajete u nevažnosti. Kucajte /start za povratak u prazninu."


# DINAMIČKI GENERISAN DRAMATIČNI TEKST KOJI SE ŠALJE POSLE POTVRDE IGRAČA (V3.28 - V ton)
def generate_dramatic_intro(player_name=None):
    if not ai_client:
        return "**Transmiter je mutan.** Ja sam Ideja. Vreme se ruši. Kucajte /pokreni."
        
    prompt = (
        f"Korisnik (Kandidat) je upravo potvrdio signal. Ti si Dimitrije (Hroničar Arhive), ali govoriš u stilu 'V' iz V for Vendetta. Oslovljavaj ga sa 'Kandidatu' ili 'Putniče'. "
        "Tvoj ton je poetski, teatralan i koristi uzvišene reči. "
        "Glavne tačke koje tvoj govor mora da obuhvati (u 3-4 poetske rečenice): "
        "1. **Predstavljanje:** Spomeni da si Hroničar Arhive i da si pronašao ključni **Dokument** o zaboravljenoj slobodi."
        "2. **Opasnost:** Dokument razotkriva **Kontrolni Sistem**, gde je sloboda samo sećanje."
        "3. **Svrha:** Primio si poziv, time si dokazao **Volju**. Moraš kroz **Pečate** otkriti **šifru** da bih predao **ključ** za Vendettu. "
        "4. **Aktivni poziv:** Završi sa 'Kucajte /pokreni'. "
        f"Ime korisnika je: {player_name if player_name else 'Nepoznat'}. **Neka odgovor bude uzvišen i pun aliteracija (V, S, M).**"
    )
    return generate_ai_response(prompt)


def generate_conversation_response(message_text, current_riddle_status, solved_count):
    if not ai_client:
        return "Moj etar je mutan. Vreme je kratko, vrati se na /zagonetka."
        
    riddle_info = "nije u Probi"
    if current_riddle_status and current_riddle_status not in ["INITIAL_WAIT_1", "INITIAL_WAIT_2"]:
        riddle_info = f"trenutno rešava Pečat mudrosti broj {solved_count + 1}"
        
    prompt = (
        f"Kandidat je poslao opštu poruku/pitanje: '{message_text}'. Ti si Dimitrije (Hroničar Arhive), ali govoriš u stilu 'V' iz V for Vendetta. "
        "Generiši **maksimalno uzvišen i dramatičan** odgovor (**maks. 2-3 rečenice**) koji: "
        "1. **Stilizovano odbija** Kandidatovu temu kao trivijalnu, naglašavajući gubljenje vremena za Vendettu. " 
        "2. Koristi prvu osobu i poetski jezik (aliteracije, filozofski ton). "
        "3. **Odmah ga vraća na misiju/Pečat.**"
    )
    return generate_ai_response(prompt)
# ... (Ostale AI funkcije generisanja odgovora će automatski preuzeti 'V' ton iz SYSTEM_INSTRUCTION) ...

# ... (Sekcije 6, 7 i svi Handleri ostaju isti, koristeći novu logiku i poruke) ...

if __name__ == '__main__':
    # Logika za pokretanje na localhostu ako nije definisan RENDER_EXTERNAL_URL
    if 'RENDER_EXTERNAL_URL' not in os.environ:
        logging.warning("Nije pronađena RENDER_EXTERNAL_URL varijabla. Pokrećem bot u polling modu (samo za testiranje!).")
        try:
            bot.remove_webhook()
            # Uključenje za logiku (V3.27) koja je primenjena u handle_commands
            global bot 
            bot.polling(none_stop=True)
        except Exception as e:
            logging.error(f"Greška pri pokretanju pollinga: {e}")
    else:
        # Podesi webhook pri pokretanju na Renderu
        @app.before_first_request
        def set_webhook_on_startup():
             webhook_url_with_token = os.environ.get('RENDER_EXTERNAL_URL').rstrip('/') + '/' + os.environ.get('BOT_TOKEN')
             s = bot.set_webhook(url=webhook_url_with_token)
             if s:
                 logging.info(f"Webhook uspešno postavljen: {webhook_url_with_token}")
             else:
                 logging.error("Neuspešno postavljanje webhooka.")
                 
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
