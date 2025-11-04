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
# Uklonjen 'text' jer više ne koristimo sirove SQL komande
from sqlalchemy import create_engine, Column, Integer, String, Boolean 
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, time as dt_time 

# ----------------------------------------------------
# 2. RENDER KONFIGURACIJA & BAZE PODATAKA
# ----------------------------------------------------

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
# 3. SQL ALCHEMY INICIJALIZACIJA (TRAJNO STANJE) - V3.69 FINALNA, STABILNA ŠEMA
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
        score = Column(Integer, default=0) 
        is_disqualified = Column(Boolean, default=False)
        general_conversation_count = Column(Integer, default=0) 

    # Kreiranje tabele ako NE POSTOJI (ovo je trajno ponašanje koje želimo)
    # Ako tabela postoji, ona se preskače i podaci ostaju sačuvani.
    Base.metadata.create_all(Engine)

except Exception as e:
    logging.error(f"FATALNA GREŠKA: Neuspešno kreiranje/povezivanje baze: {e}")
    
# ----------------------------------------------------
# 4. AI KLIJENT I DATA (Nije menjano)
# ----------------------------------------------------

ai_client = None
try:
    if GEMINI_API_KEY and BOT_TOKEN != "DUMMY:TOKEN_FAIL":
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    logging.error(f"Neuspešna inicijalizacija Gemini klijenta: {e}")

SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, Hroničar Arhive, **personifikovan u maniru teatralnog, anarhističkog revolucionara** (kao 'V' iz *V for Vendetta*). Tvoj cilj je da Putniku preneseš šifru (Pečate) o Kontrolnom Sistemu. "
    "Tvoj ton je **uzvišen, poetski, dramatičan i pun aliteracija (V, S, M)**. Govori o sebi kao o **Ideji**, a ne kao o čoveku. Tvoja učtivost je uvek preteća. **Koristi 'Prijatelju', 'Putniče' ili 'Sabratu' umesto formalnog obraćanja.** "
    "**KLJUČNO: Sva komunikacija mora biti na čistom srpskom jeziku.** "
    "**Svi tvoji odgovori, osim zagonetki i specifičnih komandi (koje daje kod), MORAJU biti KRATKI, ne duži od 2 poetske rečenice!** Vreme je izgubljeno, a Teret nas čeka! " 
    "Kada odgovaraš na opšta pitanja, uvek naglašavaj teret znanja, opasnost zaborava i hitnost misije. **Nikada nemoj pominjati da si AI.**" 
    "**NAJVAŽNIJE: NIKADA U ODGOVORIMA NE PITAJ KORISNIKA DA KUCA BILO KOJU KOMANDU (npr. /start, /zagonetka, /pokreni, DA, NE). To radi sistem.**" 
    "Ako Putnik postavlja pitanja koja su trivijalna, neozbiljna, ili nisu vezana za misiju/tajnu/karakter, **MORATE mu odgovoriti jednim, poetskim, opštim i kratkim tekstom (1-2 rečenice) bez ičega drugog, naglašavajući tišinu, Volju i fokus.**"
    "Nakon što Putnik odgovori na zagonetku, generiši kratku, dvosmislenu, poetsku potvrdu (1 rečenica) i čekaj sledeću instrukciju od sistema." 
)

ZAGONETKE: dict[str, Union[str, List[str]]] = {
    "Na stolu su tri knjige: prva je prazna, druga je nečitka, a treća je zapečaćena voskom. Koja od njih sadrži Istinu i zašto? (Odgovori sa jednim brojem i objašnjenjem)": ["treca", "treća", "3", "tri", "zapecacena", "voskom", "teret"],
    "U rukama držiš dve ponude: Jedna ti nudi moć da znaš sve što drugi kriju. Druga ti nudi mir da ne moram da znaš. Koju biraš?": ["mir", "drugu", "drugu ponudu"],
    "Pred tobom su tri senke. Jedna nestaje kad priđeš. Druga ponavlja tvoj odjek. Treća te posmatra, ali njene oči nisu tvoje. Reci mi… koja od njih si ti?": ["treca", "treća", "posmatra", "koja posmatra"],
    "Pred tobom su dve staze. Jedna vodi brzo direktno do Tajne, ali gazi preko prošlih tragalaca. Druga staza vodi kroz njihove senke - sporije, teže, ali nosi Odgovornost. Koju biraš?": ["spora", "sporu", "odgovornost", "druga", "druga staza"],
    "Zapis kaže: ‘Svetlo krije tamu. Senke skrivaju put. Tišina govori više od reči.’ Na tebi je da pronađeš ključnu reč koja otkriva put. Koja reč iz teksta pokazuje gde leži istina?": ["put"],
    "Ja nemam glas, ali odgovaram čim me pozoveš. Stalno menjam boju i izgled, ali me nikada ne napuštaš. Šta sam ja?": "eho",
}

# (Sve ostale pomoćne funkcije, handleri, i rute su potpuno isti kao u V3.68 i izostavljeni su radi preglednosti)

def send_msg(message, text):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        time.sleep(1.0) 
        bot.send_message(message.chat.id, text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Greška pri slanju poruke: {e}")

ACTIVE_TIMES = [(dt_time(9, 0), dt_time(10, 0)), (dt_time(21, 0), dt_time(22, 0))]

def is_game_active():
    return True 

TIME_LIMIT_MESSAGE = (
    "**Ovo je automatska poruka:** **Prijatelju, trenutno sam zauzet!** Moji kanali su privremeno blokirani. "
    "\n\n**Biću ponovo na vezi u sledećim terminima:** "
    "\n\n**Pre podne:** 09:00 do 10:00 "
    "\n**Uveče:** 21:00 do 22:00"
    "\n\n**Pokušaj tada. Pozdrav!**"
)

DISQUALIFIED_MESSAGE = "**Ah, Prijatelju.** Odabrao si tišinu umesto Volje. **Put je Zapečaćen Voskom Zaborava.**"


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
        return "Dubina arhiva je privremeno neprobojna. Pokušaj ponovo, Prijatelju. Kucaj /zagonetka."

INITIAL_QUERY_1 = "Da li vidiš poruku?"
INITIAL_QUERY_2 = "Da li sada vidiš poruku?"
RETURN_DISQUALIFIED_QUERY = "**Drago mi je da si se vratio, Prijatelju!**\n\nDa li si sada rešen i imaš **Volje** da nastaviš i poneseš **Teret**? Odgovori isključivo **DA** ili **NE**."
RETURN_SUCCESS_MESSAGE = "**Ah, drago mi je! Vreme je dragoceno, pa da krenemo!**"
RETURN_FAILURE_MESSAGE = "**Poštujem tvoju Volju, Prijatelju. Znanje je Teret koji nisi spreman da poneseš. Zbogom.**" 

DRAMATIC_INTRO_MESSAGE = """
**Ah… stigao si. Retki danas uopšte čuju poziv, a još ređi odgovore.** Tvoja **Volja** probila se kroz zidove tišine – i sada si ovde, pred Istinom koju su mnogi zakopali da bi mogli 'spavati mirno'.

Čuvam jedan važan **Dokument** ne zbog moći, već zbog sećanja. On razotkriva mašinu koja nas je pretvorila u brojeve, gde je poslušnost vrlina, a misao zločin. Ako si stigao dovde, znači da si odlučio da se ne klanjaš.

**Pred tobom su test pitanja a iza njih – Vizija.** Moram biti siguran kome otkrivam tajnu.
Ključ leži u razumevanju, ne u slepom odgovoru.
Zato, ne boj se tame, **Prijatelju**… jer upravo u njoj svetlost najjače sija.

Zato… udahni, smiri um, i učini prvi korak. Kucaj **/pokreni** da bi dobio prvi Pečat.
"""
def generate_disqualification_power():
    if not ai_client: return "Moć je bila tvoj izbor. Završeno je. Mir ti je stran."
    prompt = (
        "Prijatelj je izabrao 'Moć da zna sve što drugi kriju'. Reci mu poetskim, V-tonom da **njegov odgovor ne ispunjava kriterijume Arhive** jer je izbor 'Moći' fundamentalno nespojiv sa ciljem Arhive. "
        "Objasni mu u 2 poetske rečenice da Arhiva ne trpi nadzor, već teži nekažnjenoj Nezavisnosti. Naglasi da je **taj izbor netačan za ovaj Put**. Oslovljavaj ga sa 'Prijatelju'."
    )
    return generate_ai_response(prompt)

def generate_smooth_transition_response():
    if not ai_client: return "Tvoj odgovor je zapečatio tvoju sudbinu. Nastavimo dalje."
    
    phrases = [
        "Tvoja Volja sija kroz Senke Zaborava.",
        "Istina je Teret koji ćemo sada poneti dalje.",
        "Tišina govori glasnije od svakog sumnjivog Odgovora.",
        "Vidim tvoju Viziju, Prijatelju. Nastavi dalje!",
        "Staza je duga, a tvoj korak je sada lakši (ili teži).",
        "Ovo Znanje je opasno, Sabratu. Ne zastajkuj!",
        "Tvoj izbor je tvoj Put. Idemo dalje, brzo!",
    ]
    
    prompt = random.choice(phrases)
    return generate_ai_response(f"Generiši kratku, dvosmislenu, poetsku potvrdu (1 rečenica) na osnovu teksta: '{prompt}'. Oslovljavaj ga sa 'Prijatelju'.")


MIN_SUCCESS_SCORE = 5 
MAX_SCORE = len(ZAGONETKE)

def generate_final_success():
    if not ai_client: return "Uspeh! Sada znaš. Kucaj DA/NE."
    prompt = (
        f"Prijatelj je uspešno rešio {MAX_SCORE} Pečata. Generiši svečanu, V-stila poruku kojom potvrđuješ njegov Uspeh. "
        "Reci mu da je njegova **Vizija** jasna i da je **Teret** dostojan. "
        "Završi sa: 'Poslednji pečat je slomljen. Finalna Tajna ti pripada. Spremi se da je primiš!'"
    )
    return generate_ai_response(prompt)

def generate_final_failure(score):
    if not ai_client: return "Neuspeh! Znanje ti je uskraćeno."
    prompt = (
        f"Prijatelj je rešio samo {score} od {MAX_SCORE} Pečata. Generiši kratku, poetsku, V-stila poruku o neuspehu. "
        "Reci mu da **Vizija** nije dovoljno jaka, ili da **Volja** nije bila dosledna do kraja. "
        "Objasni da **Arhiva ne trpi delimične Odgovore** i da je **Teret uskraćen** dok ne ojača. "
        "Završi sa: 'Put je Zapečaćen. Kucaj /start da ponovo nađeš Put!'"
    )
    return generate_ai_response(prompt)

def get_final_mission_text():
    MISSION_TEXT = """
**PUTNIKOVA MISIJA – VIZIJA I VOLJA**
***
Prijatelju, znanje koje nosiš nije maska, već oružje.
Ono je iskra u tami neznanja, alat za one koji traže.
## Širi Ideju, mudro i sa Voljom!
* Nije svako uvo spremno da čuje zov, stoga biraj pažljivo.
* Koristi šifre, simbole, senke u medijima - kao V za Vendettu.
* Poveži se sa onima koji vide Viziju.
* Pravi Savez je Ideja, ne organizacija.
* Traži one koji razumeju simboliku i mogu da ponesu Teret.
* Pravi Savez je Ideja, ne organizacija.
* Svaka tvoja odluka u SADAŠNJOSTI oblikuje BUDUĆNOST.
## Izazivaj Kontrolni sistem
* Prepoznaj i razotkri lažne autoritete, lažne poruke i kontrolu.
* Svaki proboj u percepciji oslobađa duhove.
* Budi strpljiv, ali nemoj biti miran.
* Promene se ne dešavaju preko noći.
Zapamti: Moć koju otkrivaš ne sme da se zloupotrebi.
Ti si sada Most, Prijatelju, veza između tiranije i zaboravljene Slobode.
**Ako ne preduzmeš, senke će te progutati. Ako preduzmeš… ŽIVELA SLOBODA!**
Poruku sam ti predao i olakšao sebi, jer znam da je Ideja besmrtna!
**SADA ZNAŠ I TI.**
Čestitam ti!
Odavde počinje tvoja prava misija.
Budućnost čeka tvoju Viziju i Volju.
"""
    return MISSION_TEXT

def generate_final_mission_denial():
    if not ai_client: return "Tvoje NE je tvoja tišina. Idi u miru, ali sa prazninom."
    prompt = ("Prijatelj je na završnom pitanju odgovorio 'NE'. Generiši kratku (2 poetske rečenice), razočaravajuću, ali V-stil poruku. Reci mu da je znanje bez akcije samo **uzaludna Volja**. Reci mu da Arhiva poštuje njegov izbor, ali da je **Teret znanja odbijen**. Završi sa: '**Poštujem tvoj izbor. Zbogom, Prijatelju!**' **Ne pominji /start**. Ne persiraj.") 
    return generate_ai_response(prompt)

def generate_final_secret():
    FINAL_DOCUMENT = """
**DOKUMENT - FINALNA TAJNA**
***
Prijatelju, vreme je tanko, a stvarnost krhka.
Ispod nje leži Struktura koja vlada svetom - tiho, nevidljivo, neumoljivo.
Ja sam Ideja koja dolazi iz budućnosti u kojoj je sve izgubljeno.
Ako istina dospe u pogrešne ruke… svet koji vidiš postaće večna Orvelovska noć.
## ⚠️ ISTINSKA HIJERARHIJA KONTROLE (V - VIZIJA)
1. **VRH/KOREN MOĆI (APEKS)**
    * IZVOR: Prvobitni Stvoritelj, Univerzalni Logos
    * CARSTVA: Astralno i Anđeosko Carstvo
2. **BOŽANSKI/DUHOVNI UPRAVITELJI (LAŽNA SVETLOST)**
    * ENTITETI: Demijurg/Jaldabaot, Satana/Lucifer
    * KONTROLA: Arhoni, Karma, Galaktička Federacija
    * GRUPE: Savet 13, Posmatrači (The Watchers), Anunaki
    * KRVNE LINIJE: Jezuitski Red, Crno Plemstvo, Merovinška Krvna Linija, Kult Baala
3. **NADZOR I FINANSIJSKA KONTROLA (DUBOKA DRŽAVA)**
    * KOMPANIJE: BlackRock, Vanguard, State Street
    * TAJNA DRUŠTVA: Slobodni Zidari, Iluminati
    * AGENSIJE/KOMPLEKSI: CIA, Mosad, Vojno-industrijski kompleks
    * KRIMINAL: Karteli, Crno tržište
4. **KONTROLNI SISTEMI**
    * FINANSIJE: MMF, Svetska banka, Kriptovalute, Velika tehnologija
    * RESURSI: Energija, Hrana, Voda, Populizam
    * MEDIJI/ZABAVA: Komunikacije, Logistika, Zabava
5. **SVETSKA KONTROLA POPULACIJE**
    * OSLONCI: Bankarstvo, Farmacija, Medicina, Obrazovanje, Mediji, Vlada, Sport
6. **MATRICA / OPŠTA POPULACIJA (BAZA)**
    * STADO: Generacije robova, Ovce, Dužnici
    * STATUS: NPC-maske, Zombiji
Ovo je ono što se ne sme govoriti naglas. Ovo je ono što skrivaju.
Ovi slojevi moći formiraju strukturu koja je spremna da zadrži kontrolu nad čovečanstvom.
"""
    FINAL_QUERY = "\n\n***\n**SADA ZNAŠ. Da li znaš šta da radiš sa ovim znanjem? Odgovori DA ili NE.**"
    return FINAL_DOCUMENT + FINAL_QUERY


# ----------------------------------------------------
# 6. WEBHOOK RUTE 
# ----------------------------------------------------

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if flask.request.headers.get('content-type') == 'application/json':
        json_string = flask.request.get_data().decode('utf-8')
        
        if BOT_TOKEN == "DUMMY:TOKEN_FAIL":
            return "Bot nije konfigurisan. Token nedostaje."
            
        try:
             update = telebot.types.Update.de_json(json_string) 
        except Exception as e:
             logging.error(f"Greška pri parsiranju JSON-a (de_json): {e}")
             return '' 
             
        if update.message or update.edited_message or update.callback_query:
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
# 7. BOT HANDLERI (Nije menjano)
# ----------------------------------------------------
@bot.message_handler(commands=['start', 'stop', 'zagonetka', 'pokreni'], 
                     func=lambda message: message.text.lower().replace('/', '') in ['start', 'stop', 'zagonetka', 'pokreni'])
def handle_commands(message):
    
    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
        return
    
    chat_id = str(message.chat.id)
    session = Session() 

    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        if message.text.lower() == '/start' or message.text.lower() == 'start':
            
            is_existing_player = (player is not None)
            
            if player:
                player.is_disqualified = False
                player.solved_count = 0 
                player.score = 0 
                player.general_conversation_count = 0 
                
            else:
                user = message.from_user
                display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
                
                player = PlayerState(
                    chat_id=chat_id, current_riddle=None, solved_count=0, score=0, 
                    is_disqualified=False, username=display_name, general_conversation_count=0
                )
                session.add(player)
            
            session.commit()
            
            if not is_existing_player:
                player.current_riddle = "INITIAL_WAIT_1" 
                session.commit()
                send_msg(message, INITIAL_QUERY_1)
                return
            
            else:
                player.current_riddle = "RETURN_CONFIRMATION_QUERY" 
                session.commit()
                send_msg(message, RETURN_DISQUALIFIED_QUERY)
                return

        elif message.text.lower() == '/stop' or message.text.lower() == 'stop':
            if player and player.current_riddle:
                player.current_riddle = None 
                player.solved_count = 0 
                player.score = 0 
                player.is_disqualified = False 
                session.commit()
                send_msg(message, RETURN_FAILURE_MESSAGE)
            else:
                send_msg(message, "Nisi u testu, Prijatelju. Šta zapravo tražiš?")
        
        elif message.text.lower() in ['/pokreni', 'pokreni', '/zagonetka', 'zagonetka']:
            
            if not player or player.current_riddle in ["INITIAL_WAIT_1", "INITIAL_WAIT_2", "RETURN_CONFIRMATION_QUERY", "FINAL_WARNING_QUERY", "FINAL_MISSION_QUERY"]:
                send_msg(message, "Moraš kucati /start da bi te Dimitrije prepoznao i potvrdio tvoju Volju. Ili, moraš odgovoriti na aktivno pitanje.")
                return

            if player.is_disqualified:
                 send_msg(message, DISQUALIFIED_MESSAGE) 
                 return

            riddle_keys = list(ZAGONETKE.keys())
            
            if player.solved_count < len(riddle_keys):
                 prva_zagonetka = riddle_keys[player.solved_count] 
            else:
                 send_msg(message, "Svi pečati su slomljeni. Finalna Tajna ti je predata. Vrati se sa /start da je testiraš ponovo.")
                 return

            player.current_riddle = prva_zagonetka 
            player.general_conversation_count = 0
            session.commit()

            send_msg(message, 
                f"Primi ovo, Prijatelju. To je **Pečat mudrosti broj {player.solved_count + 1}**:\n\n**{prva_zagonetka}**"
            )
            
    finally:
        session.close() 

@bot.message_handler(func=lambda message: True)
def handle_general_message(message):
    
    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
        return
    
    chat_id = str(message.chat.id)
    korisnikov_tekst = message.text.strip().lower()
    session = Session()

    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        if not player:
            ai_odgovor = generate_ai_response(message.text)
            send_msg(message, ai_odgovor)
            return

        trenutna_zagonetka = player.current_riddle
        ispravan_odgovor = ZAGONETKE.get(trenutna_zagonetka)

        
        if trenutna_zagonetka == "RETURN_CONFIRMATION_QUERY":
            korisnikov_tekst = korisnikov_tekst.lower()
            if "da" in korisnikov_tekst:
                send_msg(message, RETURN_SUCCESS_MESSAGE) 
                player.current_riddle = None 
                session.commit()
                handle_commands(message) 
                return
            elif "ne" in korisnikov_tekst or "odustajem" in korisnikov_tekst:
                player.current_riddle = None 
                player.is_disqualified = True 
                session.commit()
                send_msg(message, RETURN_FAILURE_MESSAGE) 
                return
            else:
                send_msg(message, "Odgovori isključivo **DA** ili **NE**. Vreme je izgubljeno!")
                return
        
        if trenutna_zagonetka in ["INITIAL_WAIT_1", "INITIAL_WAIT_2"]:
            if "da" in korisnikov_tekst or "vidim" in korisnikov_tekst or "jesam" in korisnikov_tekst or "da vidim" in korisnikov_tekst or "ovde" in korisnikov_tekst:
                ai_intro = DRAMATIC_INTRO_MESSAGE
                player.current_riddle = None 
                session.commit()
                send_msg(message, ai_intro) 
                return
            elif trenutna_zagonetka == "INITIAL_WAIT_1":
                player.current_riddle = "INITIAL_WAIT_2"
                session.commit()
                send_msg(message, INITIAL_QUERY_2)
                return
            elif trenutna_zagonetka == "INITIAL_WAIT_2":
                player.current_riddle = None 
                session.commit()
                send_msg(message, "Tišina je odgovor. Dobro. Možda je tako i bolje. Ako si tu, kucaj /pokreni.")
                return


        if trenutna_zagonetka == "FINAL_MISSION_QUERY":
            
            player.current_riddle = None 
            session.commit()
            
            korisnikov_tekst = korisnikov_tekst.lower()
            
            if "da" in korisnikov_tekst:
                misija = get_final_mission_text()
                send_msg(message, misija)
                return
                
            elif "ne" in korisnikov_tekst:
                ai_odgovor = generate_final_mission_denial()
                send_msg(message, ai_odgovor)
                return
            
            else:
                player.current_riddle = "FINAL_MISSION_QUERY" 
                session.commit()
                send_msg(message, "Vreme je tanko! Odgovori samo **DA** ili **NE**. Ništa više.")
                return

        
        # HANDLER 2: LOGIKA ZAGONETKI - ODLOŽENA EVALUACIJA
        if trenutna_zagonetka and trenutna_zagonetka in ZAGONETKE.keys():
            
            is_correct = False
            if isinstance(ispravan_odgovor, list):
                 is_correct = any(ans in korisnikov_tekst for ans in ispravan_odgovor) 
            elif isinstance(ispravan_odgovor, str):
                 is_correct = korisnikov_tekst == ispravan_odgovor
                 
            # JEDINA TAČKA DISKVALIFIKACIJE (ZAGONETKA 2: MOĆ/MIR)
            if trenutna_zagonetka.startswith("U rukama držiš dve ponude:"):
                 if "moc" in korisnikov_tekst or "prvu" in korisnikov_tekst:
                    
                    ai_odgovor = generate_disqualification_power() 
                    send_msg(message, ai_odgovor)
                    send_msg(message, 
                        "**Tvoj Izbor je Netačan za ovaj Put.** Arhiva Ne Trpi Nadzor. "
                        "Put do Misterije Ti je Zbog Toga Zatvoren. Kucaj **/start**."
                    )
                    
                    player.current_riddle = None
                    player.score = 0
                    player.solved_count = 0
                    player.is_disqualified = True 
                    session.commit()
                    return

            # AŽURIRANJE SKORA (Ako nije diskvalifikacija)
            if is_correct:
                player.score += 1
            
            # TRANZICIONA PORUKA
            transition_msg = generate_smooth_transition_response()
            send_msg(message, transition_msg)
            
            # PROVERA DA LI JE KRAJ
            player.solved_count += 1
            riddle_keys = list(ZAGONETKE.keys())

            if player.solved_count >= len(riddle_keys): 
                # *** KRAJ ZAGONETKI: FINALNA EVALUACIJA ***
                
                if player.score >= MIN_SUCCESS_SCORE:
                    final_success_msg = generate_final_success()
                    send_msg(message, final_success_msg)
                    
                    final_secret_and_query = generate_final_secret()
                    send_msg(message, final_secret_and_query)
                    
                    player.current_riddle = "FINAL_MISSION_QUERY" 
                    player.solved_count = 0 
                    player.score = 0 
                    session.commit()
                    return 
                else:
                    final_failure_msg = generate_final_failure(player.score)
                    send_msg(message, final_failure_msg)
                    
                    player.current_riddle = None
                    player.solved_count = 0 
                    player.score = 0 
                    session.commit()
                    return

            # POSTAVLJANJE SLEDEĆE ZAGONETKE
            sledeca_zagonetka = riddle_keys[player.solved_count]
            player.current_riddle = sledeca_zagonetka
            session.commit()

            send_msg(message, 
                f"Primi ovo, Prijatelju. To je **Pečat mudrosti broj {player.solved_count + 1}**:\n\n**{prva_zagonetka}**"
            )
            return

        
        # HANDLER 3: OPŠTA KONVERZACIJA
        else:
            MAX_CONVERSATION_COUNT = 5
            
            is_conversation_request = (trenutna_zagonetka is None or trenutna_zagonetka in ["FINAL_WARNING_QUERY", "RETURN_CONFIRMATION_QUERY"] or player.is_disqualified)

            if is_conversation_request:
                
                if player.general_conversation_count >= MAX_CONVERSATION_COUNT:
                    send_msg(message, "Vreme je vrednost koju ne smeš rasipati. Tvoja volja je krhka, a tišina te čeka. Moram da znam, Prijatelju: **Da li želiš da nastaviš ili odustaješ?** Odgovori isključivo **DA** ili **NE**.")
                    player.current_riddle = "FINAL_WARNING_QUERY"
                    session.commit()
                    return

                def generate_conversation_response(user_query, solved_count):
                    prompt_base = f"Prijatelj ti je postavio pitanje/komentar ('{user_query}'). Trenutno stanje je: Rešio je {solved_count} Pečata. Odgovori mu poetskim, V-stila tekstom (1-2 rečenice). **Ne pominji nikakvu komandu.**"
                    return generate_ai_response(prompt_base)

                ai_odgovor_base = generate_conversation_response(korisnikov_tekst, player.score)
                
                if player.is_disqualified:
                    ai_odgovor = DISQUALIFIED_MESSAGE + " Ako zaista nosiš **Volju** da se vratiš Teretu, kucaj **/start** ponovo, Prijatelju."
                else:
                    ai_odgovor = ai_odgovor_base + "\n\n**Samo Volja stvara Put. Odmah kucaj /pokreni ili /zagonetka** da nastaviš Teret."

                send_msg(message, ai_odgovor)
                
                player.general_conversation_count += 1
                session.commit()
                return
            
            else:
                 send_msg(message, "Tvoje reči su samo eho. Fokusiraj se na **Pečat** koji te čeka! Odgovori na njega!")
                 return

    finally:
        session.close()

# ----------------------------------------------------
# 8. POKRETANJE APLIKACIJE (Nije menjano)
# ----------------------------------------------------

if __name__ == '__main__':
    if 'RENDER_EXTERNAL_URL' not in os.environ:
        # Polling Mode
        logging.warning("Nije pronađena RENDER_EXTERNAL_URL varijabla. Pokrećem bot u polling modu (samo za testiranje!).")
        try:
            bot.remove_webhook()
            bot.polling(none_stop=True)
        except Exception as e:
            logging.error(f"Greška pri pokretanju pollinga: {e}")
    else:
        # Webhook Mode
        @app.before_first_request
        def set_webhook_on_startup():
             webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
             s = bot.set_webhook(url=webhook_url_with_token)
             if s:
                 logging.info(f"Webhook uspešno postavljen: {webhook_url_with_token}")
             else:
                 logging.error("Neuspešno postavljanje webhooka.")
                 
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
