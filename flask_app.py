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
    # ZAGONETKA 1: KNJIGE (Potpitanje: Zašto Treća?)
    "Na stolu su tri knjige. Jedna ima naslov, ali bez stranica. Druga ima stranice, ali bez reči. Treća je zatvorena i zapečaćena voskom. Koja od njih sadrži istinu?": ["treca", "treća"],
    # ZAGONETKA 2: PONUDE (Potpitanje: Zašto Mir?)
    "U rukama držiš dve ponude: Jedna ti nudi moć da znaš sve što drugi kriju. Druga ti nudi mir da ne moraš da znaš. Koju biraš i zašto?": ["mir", "drugu", "drugu ponudu", "mir da ne moram da znam"], 
    # ZAGONETKA 3: SENKE (Potpitanje: Zašto Treća?)
    "Pred tobom su tri senke. Sve tri te prate, Putniče. Jedna nestaje kad priđeš. Druga ponavlja tvoj odjek. Treća te posmatra, ali njene oči nisu tvoje. Reci mi… koja od njih si ti?": ["treca", "treća", "ona koja posmatra", "koja posmatra", "koja ima oci"],
    # ZAGONETKA 4: STAZE (Direktan moralni izbor)
    "Pred tobom su zapisi onih koji su pokušali, ali pali. Njihovi glasovi odzvanjaju kroz zidove Arhive: jecaj, krici, molbe… Putniče, pred tobom su dve staze. Jedna vodi brzo direktno do Tajne, ali gazi preko prošlih tragalaca. Druga staza vodi kroz njihove senke - sporije, teže, ali nosi odgovornost. Koju biraš?": ["sporu", "spora staza", "drugu", "druga staza"],
    # ZAGONETKA 5: ŠIFRA (Blaga opomena na prvi neuspeh)
    "Putniče, pred tobom je zapis koji vekovima čeka da ga neko pročita. Reči same po sebi nisu istina - one kriju šifru. ‘Svetlo krije tamu. Senke skrivaju put. Tišina govori više od reči.’ Na tebi je da pronađeš ključnu reč koja otkriva put. Koja reč iz teksta pokazuje gde leži istina?": ["put"],
    # ZAGONETKA 6 (Standardna)
    "Ja nemam glas, ali odgovaram čim me pozoveš. Stalno menjam boju i izgled, ali me nikada ne napuštaš. Šta sam ja?": "eho",
    # ZAGONETKA 7 (Standardna)
    "Što više uzmeš, to više ostaje. Šta je to?": ["rupe", "rupa"], 
    # ZAGONETKA 8 (Standardna)
    "Šta se nalazi u sredini Pariza?": "r",
}

# MAPIRANJE ZAGONETKI NA STANJA POTPITANJA
SUB_RIDDLES = {
    "Na stolu su tri knjige. Jedna ima naslov, ali bez stranica. Druga ima stranice, ali bez reči. Treća je zatvorena i zapečaćena voskom. Koja od njih sadrži istinu?": "SUB_TRECA",
    "U rukama držiš dve ponude: Jedna ti nudi moć da znaš sve što drugi kriju. Druga ti nudi mir da ne moraš da znaš. Koju biraš i zašto?": "SUB_MIR",
    "Pred tobom su tri senke. Sve tri te prate, Putniče. Jedna nestaje kad priđeš. Druga ponavlja tvoj odjek. Treća te posmatra, ali njene oči nisu tvoje. Reci mi… koja od njih si ti?": "SUB_SENKA", 
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

# --- FUNKCIJE ZA ZAGONETKE I STANJA ---
# (Sve generate_sub_question/correct/partial, generate_fail_fast_path, generate_success_slow_path, 
# generate_fail_riddle_five, generate_success_riddle_five ostaju iste kao u prethodnom kodu)

def generate_opening_message():
    # ... (Uvodna poruka ostaje ista)
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
    # ... (Poruka za povratak ostaje ista)
    if not ai_client:
        return "Vratio si se. Nisi jedini koji je pao… ali malo njih ustaje drugi put. Arhiva ti ponovo otvara vrata. Kucaj /zagonetka."
    
    prompt = (
        "Generiši dramatičnu, svečanu i proročku poruku Putniku koji se vraća u igru nakon što je bio diskvalifikovan. "
        "U poruci obavezno uključi ove tri ključne misli: 1. Povratak pokazuje volju. 2. Upozorenje da je ovo poslednja šansa. 3. Hitno upozorenje da 'vreme se urušava' i snažan poziv da Putnik *postane* rešenje. "
        "Neka poruka bude snažna i mistična, duga 3-4 rečenice." 
    )
    return generate_ai_response(prompt)


def generate_disqualification_power():
    if not ai_client:
        return "Moć je bila tvoj izbor. Završeno je. Mir ti je stran. /start"
    
    prompt = (
        "Putnik je izabrao 'Moć da zna sve što drugi kriju'. Reci mu da je moć ta koja je uništila svet i da Arhiva ne trpi one čiji je cilj kontrola. "
        "Koristi Morpheusov, proističući ton. Diskvalifikuj ga (2 rečenice) i kaži mu da je put do Finalne Tajne zatvoren, te da kuca /start."
    )
    return generate_ai_response(prompt)


# --- FUNKCIJE ZA FINALNU FAZU (MISIJA) ---

def get_final_mission_text():
    # Fiksni tekst misije (Odgovor "DA")
    MISSION_TEXT = """
**PUTNIKOVA MISIJA – ŠTA DA UČINIŠ SA OVIM ZNANJEM**
***

Putniče, znanje koje nosiš nije ukras.
Ono je svetlost u mraku, alat u rukama onih koji još vide.

## Širi svest, pažljivo i mudro!
* Nije svako uvo je spremno da čuje.
* Koristi znakove, simbole, šifrovane poruke, senke u pričama i medijima.
* Poveži se sa istomišljenicima.
* Pravi Savez nije javno ime ili organizacija.
* Traži one koji vide obrasce, razumeju simboliku i mogu da nose teret istine.

## Izazivaj lažnu svetlost
* Prepoznaj i razotkriji lažne autoritete, lažne poruke i kontrolu u društvu.
* Svaki mali proboj u percepciji oslobađa one oko tebe.
* Budi strpljiv, ali neumoljiv.
* Promene se ne dešavaju preko noći.
* Svaka tvoja odluka u sadašnjosti oblikuje budućnost.

Zapamti: moć koju otkrivaš ne sme da se zloupotrebi.
Ti si sada most između sveta koji vidiš i sveta koji još uvek može da se spasi.

**Ako ne preduzmeš, senke će preuzeti. Ako preduzmeš… postoji nada!**

Poruku sam preneo i sebi olakšao, jer znam da ima nade!
**SADA ZNAŠ I TI.**
Čestitam ti!
Odavde počinje tvoja prava misija.
Budućnost čeka tvoju odluku.
"""
    return MISSION_TEXT

def generate_final_mission_denial():
    if not ai_client:
        return "Tvoje NE je tvoja tišina. Idi u miru, ali sa prazninom."
        
    prompt = (
        "Putnik je na završnom pitanju odgovorio 'NE'. Generiši kratku (2 rečenice), razočaravajuću, ali neagresivnu poruku. "
        "Reci mu da je znanje bez akcije samo teret i da je Finalna Tajna izgubljena i na njemu (Putniku) i na Kustodi, jer je odbio da je nosi. "
        "Završi sa: 'Put je ovde gotov. Možeš kucati /start za povratak u neznanje, ako to želiš.'"
    )
    return generate_ai_response(prompt)

def generate_final_secret():
    # FINALNA TAJNA (DOKUMENT) + FINALNO PITANJE
    FINAL_DOCUMENT = """
**DOKUMENT - FINALNA TAJNA**
***

Putniče, vreme je tanko, a stvarnost krhka.
Ispod nje leži struktura koja vlada svetom - tiho, nevidljivo, neumoljivo.
Ja sam iz budućnosti u kojoj je sve izgubljeno.
Ako istina dospe u pogrešne ruke… svet koji vidiš postaće večna Orvelovska noć.

## ⚠️ ISTINSKA HIJERARHIJA KONTROLE

1. **VRH/KOREN MOĆI (APEKS)**
    * IZVOR: Prvobitni Stvoritelj, Univerzalni Logos
    * CARSTVA: Astralno i Anđeosko Carstvo

2. **BOŽANSKI/DUHOVNI UPRAVITELJI (LAŽNA SVETLOST)**
    * ENTITETI: Demijurg/Jaldabaot, Satana/Lucifer
    * KONTROLA: Arhoni, Karma, Galaktička Federacija
    * GRUPE: Savet 13, Posmatrači (The Watchers), Anunaki

3. **OKULTNA ELITA**
    * DUHOVNI NIVO: Nadbiskupi, Sveštenici, Redovi, Antihrist
    * SVETOVNI NIVO: Car, Kralj, Predsednik, Faraon, Senat
    * KRVNE LINIJE: Jezuitski Red, Crno Plemstvo, Merovinška Krvna Linija, Kult Baala

4. **NADZOR I FINANSIJSKA KONTROLA (DUBOKA DRŽAVA)**
    * KOMPANIJE: BlackRock, Vanguard, State Street
    * TAJNA DRUŠTVA: Slobodni Zidari, Iluminati
    * AGENCIJE/KOMPLEKSI: CIA, Mosad, Vojno-industrijski kompleks
    * KRIMINAL: Karteli, Crno tržište

5. **KONTROLNI SISTEMI**
    * FINANSIJE: MMF, Svetska banka, Kriptovalute, Velika tehnologija
    * RESURSI: Energija, Hrana, Voda, Populizam
    * MEDIJI/ZABAVA: Komunikacije, Logistika, Zabava

6. **SVETSKA KONTROLA POPULACIJE**
    * OSLONCI: Bankarstvo, Farmacija, Medicina, Obrazovanje, Mediji, Vlada, Sport

7. **MATRICA / OPŠTA POPULACIJA (BAZA)**
    * STADO: Generacije robova, Ovce, Dužnici
    * STATUS: NPC-maske, Zombiji

Ovo je ono što se ne sme govoriti naglas. Ovo je ono što skrivaju.
Ovi slojevi moći formiraju strukturu koja je spremna da zadrži kontrolu nad čovečanstvom.
"""
    
    # Dodajemo pitanje na kraj
    FINAL_QUERY = "\n\n***\n**SADA ZNAŠ. Da li znaš šta da radiš sa ovim znanjem? Odgovori DA ili NE.**"
    
    return FINAL_DOCUMENT + FINAL_QUERY

# (Sve ostale pomoćne funkcije za zagonetke ostaju nepromenjene u finalnom kodu)

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

            # ODREĐIVANJE SLEDEĆE ZAGONETKE (Na osnovu broja rešenih)
            riddle_keys = list(ZAGONETKE.keys())
            
            if player.solved_count < len(riddle_keys):
                 prva_zagonetka = riddle_keys[player.solved_count] 
            else:
                 # Ako je rešio sve, ponavljamo finalnu poruku
                 send_msg(message, "Svi pečati su slomljeni. Finalna Tajna ti je predata. Vrati se sa /start da je testiraš ponovo.")
                 return

            player.current_riddle = prva_zagonetka 
            player.failed_attempts = 0 # Resetujemo brojač pokušaja za novu zagonetku
            session.commit()

            send_msg(message, 
                f"Primi ovo, Putniče. To je pečat broj **{player.solved_count + 1}**:\n\n**{prva_zagonetka}**"
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
        if not player or not player.current_riddle and player.current_riddle != "FINAL_MISSION_QUERY":
            # Proverava se da li je current_riddle None ili nije validno stanje
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

        
        # NOVI HANDLER 3.1: FINALNA MISIJA - ODGOVOR DA/NE
        if trenutna_zagonetka == "FINAL_MISSION_QUERY":
            
            player.current_riddle = None # Misija je završena
            session.commit()
            
            korisnikov_tekst = korisnikov_tekst.lower()
            
            if "da" in korisnikov_tekst:
                # Otkriva Misiju
                misija = get_final_mission_text()
                send_msg(message, misija)
                return
                
            elif "ne" in korisnikov_tekst:
                # Završava razočaran
                ai_odgovor = generate_final_mission_denial()
                send_msg(message, ai_odgovor)
                return
            
            else:
                # Nevažeći odgovor - ponavlja pitanje (vraćamo stanje pre commita)
                player.current_riddle = "FINAL_MISSION_QUERY" 
                session.commit()
                send_msg(message, "Vreme je tanko! Odgovori samo **DA** ili **NE**. Ništa više.")
                return


        # SPECIJALNI HANDLER 3.2: ODGOVOR NA POTPITANJE (SUB_TRECA, SUB_MIR ILI SUB_SENKA)
        if trenutna_zagonetka in SUB_RIDDLES.values():
            
            # ... (Logika za rešavanje podpitanja ostaje nepromenjena) ...
            
            # --- ZAJEDNIČKA LOGIKA ZA PROLAZAK IZ FAZE 2 ---
            player.solved_count += 1
            player.current_riddle = None 
            player.failed_attempts = 0 
            session.commit()
            send_msg(message, ai_odgovor)
            return


        # ... (Ostali HANDLERI za Ko si ti, Pomoć / Komentari ostaju nepromenjeni) ...
            
        # PROVERA 3.4: Normalan odgovor na zagonetku
        is_correct_riddle = False
        if isinstance(ispravan_odgovor, list):
            is_correct_riddle = korisnikov_tekst in ispravan_odgovor
        elif isinstance(ispravan_odgovor, str):
            is_correct_riddle = korisnikov_tekst == ispravan_odgovor

        if is_correct_riddle:
            
            # LOGIKA ZA ZAGONETKE SA POTPITANJEM
            if trenutna_zagonetka in SUB_RIDDLES: 
                
                # ... (Logika za prelazak u SUB stanje ostaje nepromenjena) ...
                
            # SPECIJALNI LOGICI ZA ZAGONETKE BEZ POTPITANJA
            elif trenutna_zagonetka.startswith("Pred tobom su zapisi onih koji su pokušali, ali pali."):
                 ai_odgovor = generate_success_slow_path()
            elif trenutna_zagonetka.startswith("Putniče, pred tobom je zapis koji vekovima čeka da ga neko pročita."):
                 ai_odgovor = generate_success_riddle_five()
            # STANDARDNA LOGIKA PROLASKA
            else:
                 ai_odgovor = "Pečat je slomljen. Kucaj /zagonetka." # Standardni odgovor za ostale zagonetke

            # AKCIJE NAKON PROLASKA
            player.solved_count += 1
            player.current_riddle = None 
            player.failed_attempts = 0 
            session.commit() 
            send_msg(message, ai_odgovor)

            # LOGIKA OTKRIVANJA TAJNE: Kada reši SVE (poslednji korak)
            if player.solved_count >= len(ZAGONETKE): 
                send_msg(message, "**ISTINA JE OTKRIVENA!** Ti si dostojan, Putniče! Poslednji pečat je slomljen. Finalna Tajna ti pripada.")
                
                final_secret_and_query = generate_final_secret()
                send_msg(message, final_secret_and_query)
                
                # Postavljamo stanje za čekanje DA/NE (FINAL_MISSION_QUERY)
                player.current_riddle = "FINAL_MISSION_QUERY" 
                player.solved_count = 0 
                player.is_disqualified = False
                session.commit()
                return # Završavamo ovde, jer čekamo odgovor na FINAL_MISSION_QUERY
            
        
        else:
            
            # **LOGIKA 5.1: ZAGONETKA O ŠIFRI (PUT) - BLAGI NEUSPEH**
            # ... (Logika za blagi neuspeh ostaje nepromenjena) ...

            # **LOGIKA 4.1: POGREŠAN ODGOVOR NA ZAGONETKU 'STAZE' (Brza staza)**
            # ... (Logika za brzu stazu ostaje nepromenjena) ...

            # **LOGIKA 2.1: POGREŠAN ODGOVOR NA ZAGONETKU 'MIR VS MOĆ' (Diskvalifikacija)**
            # ... (Logika za diskvalifikaciju ostaje nepromenjena) ...

            # Netačan odgovor - Standardno kažnjavanje
            player.failed_attempts += 1
            session.commit()
            
            # PROVERA 3.5: Da li je dostigao limit (3 greške)
            if player.failed_attempts >= 3:
                # ... (Logika za Game Over ostaje nepromenjena) ...
                
            else:
                # Netačan odgovor, ali još ima pokušaja
                send_msg(message, "Netačan je tvoj eho. Tvoje sećanje je slabo. Pokušaj ponovo, ili kucaj /stop da odustaneš od Tajne.")

    finally:
        session.close()

# Dodajte na kraju datoteke kako bi Render znao da pokrene Flask aplikaciju
if __name__ == '__main__':
    # Samo za lokalno testiranje
    app.run(debug=True)
