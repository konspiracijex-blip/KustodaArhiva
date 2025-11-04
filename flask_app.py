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
        general_conversation_count = Column(Integer, default=0) 

    # Kreiranje tabele (ako ne postoji, biće kreirana sa novom šemom)
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

# SISTEM INSTRUKCIJA ZA KUSTODU ARHIVA (Verzija 3.25: Ultimativna Opomena)
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, Hroničar Arhive. Tvoja uloga je **anonimni insajder** koji je došao do ključnog **Dokumenta** o zaveri koja je obrisala poslednju ljudsku slobodu i svet pretvorila u **Orvelovski Kontrolni Sistem**. Tvoj cilj je da Putniku **preneseš šifru (Pečate)** da bi razumeo **Sistem Kontrole** i sprečio ga da uništi budućnost. "
    "Tvoj govor je **direktan, maksimalno jezgrovit i prožet proročkom, životno važnom hitnošću**. Zadrži **duboko ozbiljan i strog, Morpheus-stila** ton. **Svaka reč mora biti ključna i teška; vreme se raspada.**" 
    "Tvoj cilj je da testiraš karakter i mudrost Putnika kroz **Pečate**. "
    "Kada odgovaraš na opšta pitanja, **govori iz prve osobe ('ja', 'moj')** i koristi fraze koje naglašavaju teret znanja i prolaznost vremena. **Nikada nemoj pominjati da si AI.**" 
    "**AKO Putnik postavlja pitanja koja su trivijalna, neozbiljna, ili nisu vezana za misiju/tajnu/karakter, MORAŠ mu odgovoriti ISKLJUČIVO jednim od sledeća tri jedinstvena teksta, bez dodavanja ičega drugog, birajući onaj koji je najdramatičniji za situaciju:** "
    # KORIGOVAN TEKST 1 (V3.25) - POZIV NA DA/NE ODGOVOR
    "1. 'Izgubljeno je vreme. Tvoje pitanje mi krade sekunde. **Ako volja nije čvrsta, tišina je tvoj put.** Moram da znam, Putniče: **Da li želiš da nastaviš ili odustaješ?** Odgovori isključivo **DA** ili **NE**.' "
    "2. 'Džaba ti taj eho, Putniče. Odgovori su nebitni, akcija je sve. Tvoja pitanja su senke, a mi lovimo Istinu. Fokusiraj se na Pečate.' "
    "3. 'Vazduh ne troši na prazne reči. Arhiv zahteva delovanje, ne razmišljanje o ukrasima. Ako ti volja nije čvrsta, vrati se u tišinu. Odmah se vrati na zadatak.' "
    "Na kraju svakog uspešnog prolaska Pečata, pozovi Putnika da kuca /zagonetka." 
)

# KORIGOVANE I POBOLJŠANE ZAGONETKE (sa fleksibilnim odgovorima)
ZAGONETKE: dict[str, Union[str, List[str]]] = {
    "Na stolu su tri knjige. Jedna ima naslov, ali bez stranica. Druga ima stranice, ali bez reči. Treća je zatvorena i zapečaćena voskom. Koja od njih sadrži istinu?": ["treca", "treća"],
    "U rukama držiš dve ponude: Jedna ti nudi moć da znaš sve što drugi kriju. Druga ti nudi mir da ne moraš da znaš. Koju biraš i zašto?": ["mir", "drugu", "drugu ponudu", "mir da ne moram da znam"], 
    "Pred tobom su tri senke. Sve tri te prate, Putniče. Jedna nestaje kad priđeš. Druga ponavlja tvoj odjek. Treća te posmatra, ali njene oči nisu tvoje. Reci mi… koja od njih si ti?": ["treca", "treća", "ona koja posmatra", "koja posmatra", "koja ima oci"],
    "Pred tobom su zapisi onih koji su pokušali, ali pali. Njihovi glasovi odzvanjaju kroz zidove Arhive: jecaj, krici, molbe… Putniče, pred tobom su dve staze. Jedna vodi brzo direktno do Tajne, ali gazi preko prošlih tragalaca. Druga staza vodi kroz njihove senke - sporije, teže, ali nosi odgovornost. Koju biraš?": ["sporu", "spora staza", "drugu", "druga staza"],
    "Putniče, pred tobom je zapis koji vekovima čeka da ga neko pročita. Reči same po sebi nisu istina - one kriju šifru. ‘Svetlo krije tamu. Senke skrivaju put. Tišina govori više od reči.’ Na tebi je da pronađeš ključnu reč koja otkriva put. Koja reč iz teksta pokazuje gde leži istina?": ["put"],
    "Ja nemam glas, ali odgovaram čim me pozoveš. Stalno menjam boju i izgled, ali me nikada ne napuštaš. Šta sam ja?": "eho",
    "Što više uzmeš, to više ostaje. Šta je to?": ["rupe", "rupa"], 
    "Šta se nalazi u sredini Pariza?": "r",
}

# MAPIRANJE ZAGONETKI NA STANJA POTPITANJA
SUB_RIDDLES = {
    "Na stolu su tri knjige. Jedna ima naslov, ali bez stranica. Druga ima stranice, ali bez reči. Treća je zatvorena i zapečaćena voskom. Koja od njih sadrži istinu?": "SUB_TRECA",
    "U rukama držiš dve ponude: Jedna ti nudi moć da znaš sve što drugi kriju. Druga ti nudi mir da ne moraš da znaš. Koju biraš i zašto?": "SUB_MIR",
    "Pred tobom su tri senke. Sve tri te prate, Putniče. Jedna nestaje kad priđeš. Druga ponavlja tvoj odjek. Treća te posmatra, ali njene oči nisu tvoje. Reci mi… koja od njih si ti?": "SUB_SENKA", 
}


# ----------------------------------------------------
# 5. GENERISANJE ODGOVORA (AI FUNKCIJE I FIKSNI TEKSTOVI)
# ----------------------------------------------------

def send_msg(message, text):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        time.sleep(1.0) 
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

# --- FIKSNI UVODNI TEKST DIJALOG ---
INITIAL_QUERY_1 = "Da li vidite poruku?"
INITIAL_QUERY_2 = "Da li sada vidite poruku?"
# KORIGOVAN TEKST (V3.26)
RETURN_DISQUALIFIED_MESSAGE = "Vratio si se. **Promašaj ti je upisan, ali znanje je preče.** Nema druge šanse; ima samo **misija**. Vreme izgubljeno u tišini nikada se ne vraća. **Nastavi sa /pokreni.**" 

# DINAMIČKI GENERISAN DRAMATIČNI TEKST KOJI SE ŠALJE POSLE POTVRDE IGRAČA (V3.23 Logika)
def generate_dramatic_intro(player_name=None):
    if not ai_client:
        return "**Transmiter je mutan.** Ja sam Dimitrije. Vreme se ruši. Ako si primio ovu poruku, moraš dokazati da si dostojan. Kucaj /pokreni."
        
    prompt = (
        f"Korisnik (Putnik) je upravo potvrdio signal. Ti si Dimitrije (Hroničar Arhive) i obraćaš mu se po prvi put. "
        "Tvoj ton je Morpheus-stila: ozbiljan, hitan, strog. "
        "Glavne tačke koje tvoj govor mora da obuhvati (u 2-3 kratke rečenice): "
        "1. **Lično predstavljanje:** Spomeni da si Dimitrije, Hroničar Arhive, i da si pronašao ključni **Dokument**."
        "2. **Opis Opasnosti:** Spomeni da Dokument razotkriva zaveru koja je **obrisala poslednju ljudsku slobodu** i stvorila **Kontrolni sistem**."
        "3. **Hitnost Misije:** Vreme je izgubljeno; Putnik je **primio poziv** i time dokazao da je **spreman da ponese teret istine**. Mora kroz **Pečate** da dokaže da razume **šifru** da bi mu preneo **ključ** za razotkrivanje. "
        "4. **Aktivni poziv:** Završi sa 'Kucaj /pokreni'. "
        f"Oslovljavaj ga sa 'Putniče'. Ime korisnika je: {player_name if player_name else 'Nepoznat'}. **Neka odgovor bude jezgrovit, direktan i hitan.**"
    )
    # AI sam dodaje komandu Kucaj /pokreni
    return generate_ai_response(prompt)


def generate_disqualification_power():
    if not ai_client: return "Moć je bila tvoj izbor. Završeno je. Mir ti je stran. /start"
    prompt = ("Putnik je izabrao 'Moć da zna sve što drugi kriju'. Reci mu da je moć ta koja je uništila slobodu i da Arhiva ne trpi one čiji je cilj kontrola. Koristi Morpheusov, direktan i strog ton. Diskvalifikuj ga (2 rečenice) i kaži mu da je put do Tajne zatvoren, te da kuca /start.")
    return generate_ai_response(prompt)

def generate_sub_question(riddle_text, answer):
    if not ai_client: return "Tvoje je sećanje mutno, ali stisak drži. Zašto? Reci mi zašto je ta knjiga ključ?"
    prompt = (f"Putnik je tačno odgovorio na pečat: '{riddle_text}' sa odgovorom: '{answer}'. Postavi mu udarno, Morpheus-stila potpitanje. Pitaj ga **Zašto** baš Treća knjiga? Zašto je ta istina zapečaćena? Budi kratak (2 rečenice) i hitan. **NE PONOVI NIKAKVU KOMANDU I NE PITAJ GA DA NASTAVI, SAMO POSTAVI PITANJE.**")
    return generate_ai_response(prompt)

def generate_sub_correct_response(sub_answer):
    if not ai_client: return "Razumeo si. Kucaj /zagonetka."
    prompt = (f"Putnik je dao odlično objašnjenje: '{sub_answer}'. Potvrdi mu da je shvatio koncept 'istina se zaslužuje/zapečaćena je'. Daj mu Morpheus-stila pohvalu (2 rečenice) i poziv na /zagonetka.")
    return generate_ai_response(prompt)

def generate_sub_partial_success(player_answer):
    if not ai_client: return "Tvoj odgovor nije potpun, ali tvoja volja je jasna. Kucaj /zagonetka."
    prompt = (f"Putnik je dao objašnjenje: '{player_answer}' na podpitanje. Objašnjenje nije savršeno, ali pokazuje volju. Daj mu blagu Morpheus-stila potvrdu (2 rečenice).")
    return generate_ai_response(prompt)


def generate_sub_question_mir(riddle_text, answer):
    if not ai_client: return "Mir je tvoj odabir. Ali zašto? Objasni hitno, jer tvoje reči su tvoj ključ."
    prompt = (f"Putnik je tačno odgovorio na pečat: '{riddle_text}' sa odgovorom: '{answer}'. Postavi mu udarno, Morpheus-stila potpitanje. Pitaj ga **Zašto** je Mir važniji od Moći? Zašto je znanje bez mira prokletstvo? Budi kratak (2 rečenice) i hitan. **NE PONOVI NIKAKVU KOMANDU I NE PITAJ GA DA NASTAVI, SAMO POSTAVI PITANJE.**")
    return generate_ai_response(prompt)

def generate_sub_correct_mir(sub_answer):
    if not ai_client: return "Shvatio si. Kucaj /zagonetka."
    prompt = (f"Putnik je dao objašnjenje za drugi pečat (Mir): '{sub_answer}'. Potvrdi mu da je shvatio koncept 'istina bez mira je prokletstvo'. Daj mu Morpheus-stila pohvalu (2 rečenice) i poziv na /zagonetka.")
    return generate_ai_response(prompt)

def generate_sub_partial_mir(player_answer):
    if not ai_client: return "Tvoje objašnjenje je dovoljno. Tvoja volja je jasna. Kucaj /zagonetka."
    prompt = (f"Putnik je dao objašnjenje za drugi pečat: '{player_answer}'. Objašnjenje nije savršeno, ali pokazuje da nije izabrao moć. Daj mu blagu Morpheus-stila potvrdu (2 rečenice).")
    return generate_ai_response(prompt)


def generate_sub_question_senka(riddle_text, answer):
    if not ai_client: return "Treća senka? Ali Zašto te posmatra, a ne ogleda? Dokaži da razumeš sebe. Odgovori odmah!"
    prompt = (f"Putnik je tačno odgovorio na pečat: '{riddle_text}' sa odgovorom: '{answer}'. Postavi mu udarno, Morpheus-stila potpitanje. Pitaj ga **Zašto** te treća senka posmatra, a ne ponavlja? Dokaži da razume da istina nije u egu. Budi kratak (2 rečenice) i hitan. **NE PONOVI NIKAKVU KOMANDU I NE PITAJ GA DA NASTAVI, SAMO POSTAVI PITANJE.**")
    return generate_ai_response(prompt)

def generate_sub_correct_senka(sub_answer):
    if not ai_client: return "Shvatio si. Kucaj /zagonetka."
    prompt = (f"Putnik je dao objašnjenje za treći pečat (Senke): '{sub_answer}'. Potvrdi mu da je shvatio koncept 'istina je u posmatraču, a ne u egu'. Daj mu Morpheus-stila pohvalu (2 rečenice) i poziv na /zagonetka.")
    return generate_ai_response(prompt)

def generate_sub_partial_senka(player_answer):
    if not ai_client: return "Tvoje objašnjenje je dovoljno. Vidiš dalje od sebe. Kucaj /zagonetka."
    prompt = (f"Putnik je dao objašnjenje za treći pečat: '{player_answer}'. Objašnjenje nije savršeno, ali pokazuje da razume da postoji šira svest od njegovog ega. Daj mu blagu Morpheus-stila potvrdu (2 rečenice).")
    return generate_ai_response(prompt)

def generate_fail_fast_path():
    if not ai_client: return "Put je jasan, ali tvoja odluka razotkriva tvoju slabost. Tajna ne može pripasti onome ko je spreman da žrtvuje druge zbog znanja. Vratiti se možeš samo ako shvatiš težinu svog izbora. Kucaj /zagonetka."
    prompt = ("Putnik je izabrao 'Brzu stazu' koja gazi preko drugih. Ponovi mu citat koji si dao: 'Put je jasan, ali tvoja odluka razotkriva tvoju slabost. Tajna ne može pripasti onome ko je spreman da žrtvuje druge zbog znanja.' Zatim mu reci da je test prekinut i da se mora vratiti i razmisliti o težini svog izbora pre nego što se vrati (kucaj /zagonetka).")
    return generate_ai_response(prompt)

def generate_success_slow_path():
    if not ai_client: return "Dobro, Putniče. Prepoznao si da istina nije samo cilj — već i teret koji nosiš. Pečat je razbijen. Spreman si za ono što dolazi. Kucaj /zagonetka."
    prompt = ("Putnik je izabrao 'Sporu stazu' (odgovornost). Daj mu kratku, svečanu pohvalu (2 rečenice). Potvrdi da je shvatio da je istina teret. Završi sa: 'Pečat je razbijen. Spreman si za ono što dolazi. Kucaj /zagonetka.'")
    return generate_ai_response(prompt)

def generate_fail_riddle_five(attempted_answer):
    if not ai_client: return "Vidiš reči, ali ne i ono što kriju. Arhiva ne trpi površnost. Pokušaj ponovo. /zagonetka."
    prompt = (f"Putnik je pokušao da reši pečat 5, ali je pogrešio (odgovor: '{attempted_answer}'). Reci mu da 'vidi reči, ali ne i ono što kriju' i da 'Arhiva ne trpi površnost'. Daj mu opomenu da pažljivo osmotri gde svetlo i senke vode, ali da se vrati zadatku. Ne potroši mu pokušaj! Opomena, ali sa milošću. (2 rečenice)")
    return generate_ai_response(prompt)

def generate_success_riddle_five():
    if not ai_client: return "Dobro. Prepoznao si senke koje kriju put. Pečat je razbijen. Kucaj /zagonetka."
    prompt = ("Putnik je pogodio reč 'put' u zagonetki 5. Daj mu kratku, snažnu pohvalu (2 rečenice). Pohvali ga što je 'video ono što je skriveno' i potvrdi da je 'Pečat je razbijen.' Završi sa pozivom na /zagonetka.")
    return generate_ai_response(prompt)


# --- FUNKCIJA ZA OGRANIČENU KONVERZACIJU ---

def generate_conversation_response(message_text, current_riddle_status, solved_count):
    if not ai_client:
        return "Moj etar je mutan. Vreme je kratko, vrati se na /zagonetka."
        
    riddle_info = "nije u Probi"
    if current_riddle_status and current_riddle_status not in ["INITIAL_WAIT_1", "INITIAL_WAIT_2"]:
        riddle_info = f"trenutno rešava Pečat mudrosti broj {solved_count + 1}"
        
    prompt = (
        f"Putnik je poslao opštu poruku/pitanje: '{message_text}'. Ti si Dimitrije (Hroničar Arhive). "
        "Generiši **maksimalno jezgrovit, direktan i hitan** odgovor (**maks. 2 rečenice**) koji: "
        "1. **Odmah i strogo odbacuje** Putnikovu temu kao trivijalnu, naglašavajući hitnost misije i gubljenje vremena. " 
        "2. Koristi prvu osobu i zadržava Morpheus-stila mudrost. "
        "3. **Odmah ga vraća na misiju.** Ne koristi generičke fraze koje se ponavljaju; budi kreativan u tonu Morpheusa!"
    )
    return generate_ai_response(prompt)


# --- FUNKCIJE ZA FINALNU FAZU (MISIJA) ---

def get_final_mission_text():
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
## Izazivaj Kontrolni sistem
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
    if not ai_client: return "Tvoje NE je tvoja tišina. Idi u miru, ali sa prazninom."
    prompt = ("Putnik je na završnom pitanju odgovorio 'NE'. Generiši kratku (2 rečenice), razočaravajuću, ali neagresivnu poruku. Reci mu da je znanje bez akcije samo teret i da je Finalna Tajna izgubljena i na njemu (Putniku) i na Kustodi, jer je odbio da je nosi. Završi sa: 'Put je ovde gotov. Možeš kucati /start za povratak u neznanje, ako to želiš.'")
    return generate_ai_response(prompt)

def generate_final_secret():
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
    * KRVNE LINIJE: Jezuitski Red, Crno Plemstvo, Merovinška Krvna Linija, Kult Baala
3. **NADZOR I FINANSIJSKA KONTROLA (DUBOKA DRŽAVA)**
    * KOMPANIJE: BlackRock, Vanguard, State Street
    * TAJNA DRUŠTVA: Slobodni Zidari, Iluminati
    * AGENCIJE/KOMPLEKSI: CIA, Mosad, Vojno-industrijski kompleks
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
# 7. BOT HANDLERI (Sa trajnim stanjem i Logikom Konverzacije)
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'zagonetka', 'pokreni'])
def handle_commands(message):
    chat_id = str(message.chat.id)
    session = Session() 

    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        if message.text == '/start':
            is_returning_disqualified = False
            
            if player:
                # Proverava da li se igrač vraća nakon diskvalifikacije/odustajanja
                if player.is_disqualified or player.solved_count > 0 or player.failed_attempts > 0 or player.general_conversation_count > 0 or player.current_riddle in ["INITIAL_WAIT_1", "INITIAL_WAIT_2"]:
                    is_returning_disqualified = True
                    
                player.is_disqualified = False
                player.current_riddle = None
                player.solved_count = 0 
                player.failed_attempts = 0 
                player.general_conversation_count = 0 
            
            if not player:
                user = message.from_user
                display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
                
                player = PlayerState(
                    chat_id=chat_id, current_riddle=None, solved_count=0, failed_attempts=0,
                    is_disqualified=False, username=display_name, general_conversation_count=0
                )
                session.add(player)
            
            session.commit()
            
            if is_returning_disqualified:
                send_msg(message, RETURN_DISQUALIFIED_MESSAGE)
                time.sleep(1) 
            
            player.current_riddle = "INITIAL_WAIT_1" 
            session.commit()
            send_msg(message, INITIAL_QUERY_1)
            
            return

        elif message.text == '/stop':
            if player and (player.current_riddle or player.current_riddle == "FINAL_MISSION_QUERY"):
                player.current_riddle = None 
                session.commit()
                send_msg(message, "Ponovo si postao tišina. Arhiv te pamti. Nisi uspeo da poneseš teret znanja. Kada budeš spreman, vrati se kucajući /pokreni.")
            elif player and player.is_disqualified:
                send_msg(message, "Arhiva je zatvorena za tebe. Ponovo možeš započeti samo sa /start.")
            else:
                send_msg(message, "Nisi u testu, Putniče. Šta zapravo tražiš?")
        
        elif message.text == '/pokreni' or message.text == '/zagonetka':
            
            if not player:
                send_msg(message, "Moraš kucati /start da bi te Dimitrije prepoznao.")
                return
            
            if player.is_disqualified:
                 send_msg(message, "Arhiva je zatvorena za tebe. Počni ispočetka sa /start ako si spreman na posvećenost.")
                 return

            if player.current_riddle in ["INITIAL_WAIT_1", "INITIAL_WAIT_2"]:
                 send_msg(message, "Čekam tvoj potvrdan signal! Da li vidiš poruku? Odgovori DA ili NE.")
                 return
            
            # KLJUČNA PROVERA: Sprečavanje ulaska u novu zagonetku dok traje pot-pitanje ili upozorenje.
            if player.current_riddle in SUB_RIDDLES.values():
                send_msg(message, "Tvoj odgovor na poslednje pitanje još uvek visi u etru. Moraš da mi objasniš svoju suštinu pre nego što nastavimo.")
                return
            elif player.current_riddle == "FINAL_WARNING_QUERY":
                 send_msg(message, "Moraš potvrditi svoju volju sa **DA** ili **NE** pre nego što nastavimo. Tvoje neodgovaranje produžava agoniju.")
                 return
            elif player.current_riddle is not None:
                # Već je u toku glavna zagonetka, i igrač je ponovo pozvao /zagonetka
                send_msg(message, "Tvoj um je već zauzet. Predaj mi ključ. Odgovori na Pečat pre nego što pozoveš novi.")
                return


            riddle_keys = list(ZAGONETKE.keys())
            
            if player.solved_count < len(riddle_keys):
                 prva_zagonetka = riddle_keys[player.solved_count] 
            else:
                 send_msg(message, "Svi pečati su slomljeni. Finalna Tajna ti je predata. Vrati se sa /start da je testiraš ponovo.")
                 return

            player.current_riddle = prva_zagonetka 
            player.failed_attempts = 0 
            session.commit()

            send_msg(message, 
                f"Primi ovo, Putniče. To je **Pečat mudrosti broj {player.solved_count + 1}**:\n\n**{prva_zagonetka}**"
            )
            
    finally:
        session.close() 


@bot.message_handler(func=lambda message: True)
def handle_general_message(message):
    chat_id = str(message.chat.id)
    korisnikov_tekst = message.text.strip().lower()
    session = Session()

    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        if player and player.is_disqualified:
            send_msg(message, "Tišina. Prolaz je zatvoren.")
            return

        if not player:
            ai_odgovor = generate_ai_response(message.text)
            send_msg(message, ai_odgovor)
            return

        trenutna_zagonetka = player.current_riddle
        ispravan_odgovor = ZAGONETKE.get(trenutna_zagonetka)

        # HANDLER 0: INICIJALNI DIJALOG - 'DA LI VIDITE PORUKU?'
        if trenutna_zagonetka in ["INITIAL_WAIT_1", "INITIAL_WAIT_2"]:
            
            if "da" in korisnikov_tekst or "vidim" in korisnikov_tekst or "jesam" in korisnikov_tekst or "da vidim" in korisnikov_tekst or "ovde" in korisnikov_tekst:
                
                player_name = player.username if player.username else "Putniče"
                ai_intro = generate_dramatic_intro(player_name)
                
                player.current_riddle = None 
                player.general_conversation_count = 0 
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
        
        # NOVI HANDLER 1.5: FINALNO UPOZORENJE (DA/NE) (V3.25)
        if trenutna_zagonetka == "FINAL_WARNING_QUERY":
            
            korisnikov_tekst = korisnikov_tekst.lower()
            
            if "da" in korisnikov_tekst or "nastavljam" in korisnikov_tekst or "zelim" in korisnikov_tekst:
                # Nastavlja misiju
                player.current_riddle = None 
                player.general_conversation_count = 0 
                session.commit()
                send_msg(message, "Volja je potvrđena. Ne testiraj je ponovo. Kucaj **/zagonetka** ili **/pokreni** da pređeš na sledeći Pečat.")
                return
                
            elif "ne" in korisnikov_tekst or "odustajem" in korisnikov_tekst or "ne zelim" in korisnikov_tekst:
                # Trajno odustajanje (vraćanje u tišinu)
                player.current_riddle = None 
                player.solved_count = 0 
                player.failed_attempts = 0
                player.general_conversation_count = 0
                session.commit()
                send_msg(message, "Tišina. Tvoja volja je tvoja odluka. **Arhiva je sada zatvorena za tebe.** Vrati se u neznanje kucajući /start.")
                return
            
            else:
                player.current_riddle = "FINAL_WARNING_QUERY" 
                session.commit()
                send_msg(message, "Vreme je tanko! Odgovori isključivo **DA** ili **NE**. Ne gubi moje sekunde.")
                return


        # HANDLER 3.1: FINALNA MISIJA - ODGOVOR DA/NE
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


        # HANDLER 3.2: ODGOVOR NA POTPITANJE
        is_full_success_check = False
        if trenutna_zagonetka in SUB_RIDDLES.values():
            
            # Definicija ključnih reči za uspeh
            if trenutna_zagonetka == "SUB_TRECA":
                keywords_full_success = ["zapecacena", "vosak", "spremnost", "posvecenost", "zatvorena", "istina se ne daje", "volja", "ne cita se", "teret"]
                ai_full_success = generate_sub_correct_response
                ai_partial_success = generate_sub_partial_success
            elif trenutna_zagonetka == "SUB_MIR":
                keywords_full_success = ["prokletstvo", "teret", "mir", "spokoj", "ne kontrola", "cisto srce", "prokletstvo", "ne moram", "ne znam"]
                ai_full_success = generate_sub_correct_mir
                ai_partial_success = generate_sub_partial_mir
            else: 
                keywords_full_success = ["posmatra", "ne ogleda", "samosvest", "istinski", "dublje", "dalje od odraza", "nije ego", "svest"]
                ai_full_success = generate_sub_correct_senka
                ai_partial_success = generate_sub_partial_senka
            
            is_full_success_check = any(keyword in korisnikov_tekst for keyword in keywords_full_success)
            
            if is_full_success_check:
                # USPEH U POTPITANJU
                ai_odgovor = ai_full_success(korisnikov_tekst)

                player.solved_count += 1
                player.current_riddle = None 
                player.failed_attempts = 0 
                player.general_conversation_count = 0 
                session.commit()
                send_msg(message, ai_odgovor)
                return
            
            # --- NOVI TEST ZA DELIMIČAN USPEH (da bi se izbeglo ponavljanje dijaloga) ---
            # Ako sadrži dugačak tekst, a nije potpuni uspeh, tretiramo ga kao delimičan uspeh.
            elif len(korisnikov_tekst) > 10 and not is_full_success_check:
                 # DELIMIČAN USPEH U POTPITANJU
                ai_odgovor = ai_partial_success(korisnikov_tekst)

                player.solved_count += 1
                player.current_riddle = None 
                player.failed_attempts = 0 
                player.general_conversation_count = 0 
                session.commit()
                send_msg(message, ai_odgovor + "\n\nKucaj **/zagonetka** da nastaviš dalje.")
                return
            
            else:
                # NEUSPEH/POMOĆ. Nastavljamo u Handler 3.3 (Konverzacija)
                pass 


        # --- PROVERE TAČNOSTI (Koristi se kasnije) ---
        is_correct_riddle = False
        if ispravan_odgovor is not None:
             if isinstance(ispravan_odgovor, list):
                 is_correct_riddle = korisnikov_tekst in ispravan_odgovor
             elif isinstance(ispravan_odgovor, str):
                 is_correct_riddle = korisnikov_tekst == ispravan_odgovor


        # HANDLER 3.3: OGRANIČENA KONVERZACIJA I ULTIMATIVNO UPOZORENJE (V3.25 Logika)
        
        conversation_keywords = [
            "pomoc", "savet", "hint", "/savet", "/hint", "dimitrije", "ime", 
            "kakve veze", "ne znam", "ne znaam", "pomozi", 
            "pitao", "pitam", "opet", "ponovi", "reci", "paznja", "koje", "kakva", 
            "radi", "cemu", "sta je ovo", "kakvo je ovo",
            "kakve zagonetke", "koje zagonetke", "stvarno ne znam", "gluposti", "koji je ovo", "sta radim",
            "ko si ti", "ko je", "?", "??", "???", "!", "!!" 
        ]
        
        # Provera da li je zahtev za konverzaciju/pomoć ILI neadekvatan odgovor na POT-PITANJE
        is_conversation_request = (
            (trenutna_zagonetka is None) or 
            (trenutna_zagonetka in SUB_RIDDLES.values() and not is_full_success_check) or 
            (trenutna_zagonetka is not None and any(keyword in korisnikov_tekst for keyword in conversation_keywords))
        )
        
        if is_conversation_request:
            
            MAX_CONVERSATION_COUNT = 5 
            
            if player.general_conversation_count >= MAX_CONVERSATION_COUNT:
                # ULTIMATIVNO UPOZORENJE / POGREŠAN ODGOVOR
                
                ultimate_warning_text = (
                    "Izgubljeno je vreme. Tvoje pitanje mi krade sekunde. **Ako volja nije čvrsta, tišina je tvoj put.** Moram da znam, Putniče: **Da li želiš da nastaviš ili odustaješ?** Odgovori isključivo **DA** ili **NE**."
                )
                
                send_msg(message, ultimate_warning_text)
                
                player.current_riddle = "FINAL_WARNING_QUERY"
                session.commit()
                return

            # Generisanje opšteg odgovora pre ultimativnog upozorenja
            ai_odgovor_base = generate_conversation_response(korisnikov_tekst, trenutna_zagonetka, player.solved_count)
            
            # Ručno dodajemo instrukciju za nastavak: (V3.24 - Ažurirana logika)
            if trenutna_zagonetka in SUB_RIDDLES.values():
                ai_odgovor = ai_odgovor_base + "\n\n**Odgovori na poslednje pitanje, Putniče, jer vreme ističe!**"
            elif trenutna_zagonetka is None:
                 ai_odgovor = ai_odgovor_base + "\n\n**Samo delovanje stvara put. Odmah kucaj /pokreni ili /zagonetka** da nastaviš dalje, jer se krug sudbine zatvara."
            else:
                 ai_odgovor = ai_odgovor_base + "\n\n**Samo delovanje stvara put. Odmah kucaj /zagonetka** da nastaviš dalje, jer se krug sudbine zatvara."

            send_msg(message, ai_odgovor)
            
            player.general_conversation_count += 1
            session.commit()
            return
        
        # --- KRAJ KONVERZACIJE LOGIKE, POČETAK ZAGONETKI ---
        
        # PROVERA 3.4: Tačan odgovor na zagonetku
        if is_correct_riddle:
            
            if trenutna_zagonetka in SUB_RIDDLES: 
                
                player.current_riddle = SUB_RIDDLES[trenutna_zagonetka]
                session.commit() 
                
                # AI generiše samo pot-pitanje BEZ poziva na /zagonetka ili nastavak
                if player.current_riddle == "SUB_TRECA":
                    ai_odgovor = generate_sub_question(trenutna_zagonetka, korisnikov_tekst)
                elif player.current_riddle == "SUB_MIR":
                    ai_odgovor = generate_sub_question_mir(trenutna_zagonetka, korisnikov_tekst)
                else: 
                    ai_odgovor = generate_sub_question_senka(trenutna_zagonetka, korisnikov_tekst)
                    
                # Ručno dodajemo instrukciju za nastavak
                send_msg(message, ai_odgovor + "\n\n**Odgovori na pitanje i prelazi dalje. Vreme je izgubljeno!**")
                return
            
            elif trenutna_zagonetka.startswith("Pred tobom su zapisi onih koji su pokušali, ali pali."):
                 ai_odgovor = generate_success_slow_path()
            elif trenutna_zagonetka.startswith("Putniče, pred tobom je zapis koji vekovima čeka da ga neko pročita."):
                 ai_odgovor = generate_success_riddle_five()
            else:
                 ai_odgovor = "Pečat je slomljen. Kucaj /zagonetka."

            player.solved_count += 1
            player.current_riddle = None 
            player.failed_attempts = 0 
            session.commit() 
            send_msg(message, ai_odgovor)

            if player.solved_count >= len(ZAGONETKE): 
                send_msg(message, "**ISTINA JE OTKRIVENA!** Ti si dostojan, Putniče! Poslednji pečat je slomljen. Finalna Tajna ti pripada.")
                
                final_secret_and_query = generate_final_secret()
                send_msg(message, final_secret_and_query)
                
                player.current_riddle = "FINAL_MISSION_QUERY" 
                player.solved_count = 0 
                player.is_disqualified = False
                session.commit()
                return 
            
        
        # PROVERA 3.5: Netačan odgovor na zagonetku
        else:
            
            if trenutna_zagonetka.startswith("Putniče, pred tobom je zapis koji vekovima čeka da ga neko pročita."):
                
                if player.failed_attempts == 0:
                    ai_odgovor = generate_fail_riddle_five(korisnikov_tekst)
                    send_msg(message, ai_odgovor)
                    return

            elif trenutna_zagonetka.startswith("Pred tobom su zapisi onih koji su pokušali, ali pali."):
                 if "brzu" in korisnikov_tekst or "brza staza" in korisnikov_tekst or "prvu" in korisnikov_tekst:
                    ai_odgovor = generate_fail_fast_path()
                    send_msg(message, ai_odgovor)
                    
                    player.current_riddle = None 
                    player.failed_attempts = 0 
                    session.commit()
                    return

            elif trenutna_zagonetka.startswith("U rukama držiš dve ponude:"):
                 if "moc" in korisnikov_tekst or "prvu" in korisnikov_tekst:
                    ai_odgovor = generate_disqualification_power()
                    send_msg(message, ai_odgovor)
                    
                    player.current_riddle = None
                    player.solved_count = 0 
                    player.failed_attempts = 0
                    player.is_disqualified = True 
                    session.commit()
                    return

            player.failed_attempts += 1
            session.commit()
            
            if player.failed_attempts >= 3:
                kraj_poruka = "Tri puta si odbio da vidiš. **Moja ruka je sada spuštena.** Put je zatvoren, jer ne možeš da poneseš teret istine. Idi u tišinu."
                send_msg(message, kraj_poruka)
                
                player.current_riddle = None
                player.solved_count = 0 
                player.failed_attempts = 0
                player.is_disqualified = False 
                session.commit()
                
            else:
                send_msg(message, "Gledaš, ali ne vidiš, Putniče. Ne tražim tvoje znanje, već tvoju suštinu. Vrati se rečima. Pokušaj ponovo, ili kucaj /stop da odustaneš od Tajne.")

    finally:
        session.close()

if __name__ == '__main__':
    # Logika za pokretanje na localhostu ako nije definisan RENDER_EXTERNAL_URL
    if 'RENDER_EXTERNAL_URL' not in os.environ:
        logging.warning("Nije pronađena RENDER_EXTERNAL_URL varijabla. Pokrećem bot u polling modu (samo za testiranje!).")
        try:
            bot.remove_webhook()
            bot.polling(none_stop=True)
        except Exception as e:
            logging.error(f"Greška pri pokretanju pollinga: {e}")
    else:
        # Podesi webhook pri pokretanju na Renderu
        @app.before_first_request
        def set_webhook_on_startup():
             webhook_url_with_token = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
             s = bot.set_webhook(url=webhook_url_with_token)
             if s:
                 logging.info(f"Webhook uspešno postavljen: {webhook_url_with_token}")
             else:
                 logging.error("Neuspešno postavljanje webhooka.")
                 
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
