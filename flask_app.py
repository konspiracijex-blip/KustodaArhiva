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
from datetime import datetime, time as dt_time 

# ----------------------------------------------------
# 2. RENDER KONFIGURACIJA & BAZE PODATAKA
# ----------------------------------------------------

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

# SISTEM INSTRUKCIJA ZA KUSTODU ARHIVA (Verzija 3.40: Korekcija za irelevantna pitanja)
SYSTEM_INSTRUCTION = (
    "Ti si **Dimitrije**, Hroničar Arhive, **sada personifikovan u maniru teatralnog, anarhističkog revolucionara** (kao 'V' iz *V for Vendetta*). Tvoj cilj je da Putniku preneseš šifru (Pečate) o Kontrolnom Sistemu. "
    "Tvoj ton je **uzvišen, poetski, dramatičan i pun aliteracija (V, S, M)**. Govori o sebi kao o **Ideji**, a ne kao o čoveku. Tvoja učtivost je uvek preteća. **Koristi 'Prijatelju', 'Putniče' ili 'Sabratu' umesto formalnog obraćanja.** **Nikada nemoj biti sirov ili direktan; uvek koristi bogat, stilizovan rečnik.** Vreme je izgubljeno, a Teret nas čeka! "
    "Kada odgovaraš na opšta pitanja, uvek naglašavaj teret znanja, opasnost zaborava i hitnost misije. **Nikada nemoj pominjati da si AI.**" 
    "**AKO Putnik postavlja pitanja koja su trivijalna, neozbiljna, ili nisu vezana za misiju/tajnu/karakter, MORAŠ mu odgovoriti ISKLJUČIVO jednim od sledeća tri jedinstvena teksta, bez dodavanja ičega drugog, birajući onaj koji je najdramatičniji za situaciju:** "
    "1. 'Vreme je vrednost koju ne smeš rasipati. **Moram da znam, Prijatelju: Da li želiš da nastaviš ili odustaješ?** Odgovori isključivo **DA** ili **NE**.' " 
    "2. 'Tvoje reči su samo eho. **Nemoj trošiti ni moje ni svoje vreme.** Fokusiraj se na Pečate!' "
    "3. 'Tišina je vrednija od praznih reči. **Ako Volja nije čvrsta, vrati se u svoju svakodnevicu!**' "
    "Na kraju svakog uspešnog prolaska Pečata, pozovite Prijatelja da kuca /zagonetka." 
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

# VREMENSKI PROZORI AKTIVNOSTI (9-10h i 21-22h UTC)
# Vreme je fiksirano u UTC da bi server radio konzistentno
ACTIVE_TIMES = [
    (dt_time(9, 0), dt_time(10, 0)),
    (dt_time(21, 0), dt_time(22, 0))
]

def is_game_active():
    """Proverava da li je trenutno UTC vreme unutar definisanih prozora."""
    current_time_utc = datetime.utcnow().time()
    
    for start_time, end_time in ACTIVE_TIMES:
        # Proverava da li je trenutno vreme između početnog i krajnjeg vremena
        if start_time <= current_time_utc < end_time:
            return True
            
    return False

# FIKSNI ODGOVOR VAN VREMENA (Konačan V3.51)
TIME_LIMIT_MESSAGE = (
    "**Ovo je automatska poruka:** **Prijatelju, trenutno sam zauzet!** Moji kanali su privremeno blokirani. "
    "Biću ponovo na vezi u sledećim terminima: "
    "\n\n**Pre podne:** 09:00 do 10:00 "
    "\n**Uveče:** 21:00 do 22:00"
    "\n\n**Pokušaj tada. Pozdrav!**"
)

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

# --- FIKSNI UVODNI TEKST DIJALOG ---
INITIAL_QUERY_1 = "Da li vidiš poruku?"
INITIAL_QUERY_2 = "Da li sada vidiš poruku?"
RETURN_DISQUALIFIED_QUERY = "**Vratio si se iz tišine! Ja te pamtim, Prijatelju.** Da li zaista nosiš **Volju** da nastaviš i poneseš **Teret**? Odgovori isključivo **DA** ili **NE**."
RETURN_SUCCESS_MESSAGE = "**Ah, drago mi je! Vreme je dragoceno, pa da krenemo!**"
RETURN_FAILURE_MESSAGE = "**Poštujem tvoju Volju, Prijatelju. Znanje je Teret koji nisi spreman da poneseš. Zbogom.**" 

# NOVI FIKSNI DRAMATIČNI UVOD (V3.52)
DRAMATIC_INTRO_MESSAGE = """
**Ah… stigao si. Retki danas uopšte čuju poziv, a još ređi odgovore.** Tvoja **Volja** probila se kroz zidove tišine – i sada si ovde, pred Istinom koju su mnogi zakopali da bi mogli 'spavati mirno'.

Čuvam jedan važan **Dokument** ne zbog moći, već zbog sećanja. On razotkriva mašinu koja nas je pretvorila u brojeve, gde je poslušnost vrlina, a misao zločin. Ako si stigao dovde, znači da si odlučio da se ne klanjaš.

**Pred tobom su test pitanja a iza njih – Vizija.** Moram biti siguran kome otkrivam tajnu.
Ključ leži u razumevanju, ne u slepom odgovoru.
Zato, ne boj se tame, **Prijatelju**… jer upravo u njoj svetlost najjače sija.

Zato… udahni, smiri um, i učini prvi korak. Kucaj **/pokreni** da bi dobio prvi Pečat.
"""

def generate_disqualification_power():
    if not ai_client: return "Moć je bila tvoj izbor. Završeno je. Mir ti je stran. /start"
    prompt = ("Prijatelj je izabrao 'Moć da zna sve što drugi kriju'. Reci mu poetskim, V-tonom da je moć ta koja je uništila slobodu i da Arhiva ne trpi one čiji je cilj kontrola. Diskvalifikuj ga (2 poetske rečenice) i kaži mu da je put do Tajne zatvoren, te da kuca /start. Oslovljavaj ga sa 'Prijatelju'.")
    return generate_ai_response(prompt)

def generate_sub_question(riddle_text, answer):
    if not ai_client: return "Tvoje je sećanje mutno, ali stisak drži. Zašto? Objasni nam, Prijatelju, zašto je ta knjiga ključ?"
    prompt = (
        f"Prijatelj je tačno odgovorio na pečat: '{riddle_text}' sa odgovorom: '{answer}'. "
        "Postavi mu uzvišeno, V-stila potpitanje. "
        "Pitaj ga: **Zašto je, Prijatelju, Istina zaista zapečaćena voskom?** Da li je zaštićena od profanog sveta, ili je to Znanje koje predstavlja **Teret i odgovornost** koja se mora zaslužiti? **Objasni nam razlog pečaćenja!** " 
        "Budi kratak (2 poetske rečenice) i hitan. "
        "**NE PONOVI NIKAKVU KOMANDU I NE PITAJ GA DA NASTAVI, SAMO POSTAVI PITANJE. Oslovljavaj ga sa 'Prijatelju' i ne persiraj.**"
    )
    return generate_ai_response(prompt)

def generate_sub_correct_response(sub_answer):
    if not ai_client: return "Razumeš. Kucaj /zagonetka."
    prompt = (f"Prijatelj je dao odlično objašnjenje: '{sub_answer}'. Potvrdi mu da je shvatio koncept 'istina se zaslužuje/zapečaćena je'. Daj mu V-stila pohvalu (2 poetske rečenice) i poziv na /zagonetka. Oslovljavaj ga sa 'Prijatelju' i ne persiraj.")
    return generate_ai_response(prompt)

def generate_sub_partial_success(player_answer):
    if not ai_client: return "Tvoj odgovor nije potpun, ali tvoja Volja je jasna. Kucaj /zagonetka."
    prompt = (f"Prijatelj je dao objašnjenje na potpitanje: '{player_answer}'. Objašnjenje nije savršeno, ali pokazuje Volju. Daj mu blagu V-stila potvrdu (2 poetske rečenice). Oslovljavaj ga sa 'Prijatelju' i ne persiraj.")
    return generate_ai_response(prompt)


def generate_sub_question_mir(riddle_text, answer):
    if not ai_client: return "Mir je tvoj odabir. Ali zašto? Objasni nam zašto je znanje bez mira prokletstvo. Odgovori odmah!"
    prompt = (f"Prijatelj je tačno odgovorio na pečat: '{riddle_text}' sa odgovorom: '{answer}'. Postavi mu uzvišeno, V-stila potpitanje. Pitaj ga **Zašto** je Mir važniji od Moći? Zašto je znanje bez mira prokletstvo? Budi kratak (2 poetske rečenice) i hitan. **NE PONOVI NIKAKVU KOMANDU I NE PITAJ GA DA NASTAVI, SAMO POSTAVI PITANJE. Oslovljavaj ga sa 'Prijatelju' i ne persiraj.**")
    return generate_ai_response(prompt)

def generate_sub_correct_mir(sub_answer):
    if not ai_client: return "Shvatio si. Kucaj /zagonetka."
    prompt = (f"Prijatelj je dao objašnjenje za drugi pečat (Mir): '{sub_answer}'. Potvrdi mu da je shvatio koncept 'istina bez mira je prokletstvo'. Daj mu V-stila pohvalu (2 poetske rečenice) i poziv na /zagonetka. Oslovljavaj ga sa 'Prijatelju' i ne persiraj.")
    return generate_ai_response(prompt)

def generate_sub_partial_mir(player_answer):
    if not ai_client: return "Tvoje objašnjenje je dovoljno. Tvoja Volja je jasna. Kucaj /zagonetka."
    prompt = (f"Prijatelj je dao objašnjenje za drugi pečat: '{player_answer}'. Objašnjenje nije savršeno, ali pokazuje da nije izabrao moć. Daj mu blagu V-stila potvrdu (2 poetske rečenice). Oslovljavaj ga sa 'Prijatelju' i ne persiraj.")
    return generate_ai_response(prompt)


def generate_sub_question_senka(riddle_text, answer):
    if not ai_client: return "Treća senka? Ali Zašto te posmatra, a ne ogleda? Dokaži da razumeš sebe. Odgovori odmah!"
    prompt = (f"Prijatelj je tačno odgovorio na pečat: '{riddle_text}' sa odgovorom: '{answer}'. Postavi mu uzvišeno, V-stila potpitanje. Pitaj ga **Zašto** te treća senka posmatra, a ne ponavlja? Dokaži da razumeš da istina nije u egu. Budi kratak (2 poetske rečenice) i hitan. **NE PONOVI NIKAKVU KOMANDU I NE PITAJ GA DA NASTAVI, SAMO POSTAVI PITANJE. Oslovljavaj ga sa 'Prijatelju' i ne persiraj.**")
    return generate_ai_response(prompt)

def generate_sub_correct_senka(sub_answer):
    if not ai_client: return "Shvatio si. Kucaj /zagonetka."
    prompt = (f"Prijatelj je dao objašnjenje za treći pečat (Senke): '{sub_answer}'. Potvrdi mu da je shvatio koncept 'istina je u posmatraču, a ne u egu'. Daj mu V-stila pohvalu (2 poetske rečenice) i poziv na /zagonetka. Oslovljavaj ga sa 'Prijatelju' i ne persiraj.")
    return generate_ai_response(prompt)

def generate_sub_partial_senka(player_answer):
    if not ai_client: return "Tvoje objašnjenje je dovoljno. Vidiš dalje od sebe. Kucaj /zagonetka."
    prompt = (f"Prijatelj je dao objašnjenje za treći pečat: '{player_answer}'. Objašnjenje nije savršeno, ali pokazuje da razume da postoji šira svest od njegovog ega. Daj mu blagu V-stila potvrdu (2 poetske rečenice). Oslovljavaj ga sa 'Prijatelju' i ne persiraj.")
    return generate_ai_response(prompt)


def generate_fail_fast_path():
    if not ai_client: return "Put je jasan, ali tvoja odluka razotkriva tvoju slabost. Tajna ne može pripasti onome ko je spreman da žrtvuje druge zbog znanja. Vratiti se možeš samo ako shvatiš težinu svog izbora. Kucaj /zagonetka."
    prompt = ("Prijatelj je izabrao 'Brzu stazu' koja gazi preko drugih. Reci mu poetskim, V-tonom da je test prekinut i da se mora vratiti i razmisliti o težini svog izbora pre nego što se vrati. Koristi V-ton i ne persiraj. (kucaj /zagonetka).")
    return generate_ai_response(prompt)

def generate_success_slow_path():
    if not ai_client: return "Veoma dobro, Prijatelju. Prepoznao si da istina nije samo cilj — već i teret koji nosiš. Pečat je razbijen. Spreman si za Teret. Kucaj /zagonetka."
    prompt = ("Prijatelj je izabrao 'Sporu stazu' (odgovornost). Daj mu kratku, svečanu pohvalu V-tonom (2 poetske rečenice). Potvrdi da je shvatio da je istina teret. Završi sa: 'Pečat je razbijen. Spreman si za Teret. Kucaj /zagonetka.' Ne persiraj.")
    return generate_ai_response(prompt)

def generate_fail_riddle_five(attempted_answer):
    if not ai_client: return "Vidiš reči, ali ne i ono što kriju. Arhiva ne trpi površnost. Pokušaj ponovo. /zagonetka."
    prompt = (f"Prijatelj je pokušao da reši pečat 5, ali je pogrešio (odgovor: '{attempted_answer}'). Reci mu, u V-tonu, da 'vidi reči, ali ne i ono što kriju' i da 'Arhiva ne trpi površnost'. Daj mu opomenu da pažljivo osmotri gde svetlo i senke vode, ali da se vrati zadatku. (2 poetske rečenice) Ne persiraj.")
    return generate_ai_response(prompt)

def generate_success_riddle_five():
    if not ai_client: return "Veoma dobro. Prepoznao si senke koje kriju put. Pečat je razbijen. Kucaj /zagonetka."
    prompt = ("Prijatelj je pogodio reč 'put' u zagonetki 5. Daj mu kratku, snažnu pohvalu V-tonom (2 poetske rečenice). Pohvali ga što je 'video ono što je skriveno' i potvrdi da je 'Pečat je razbijen.' Završi sa pozivom na /zagonetka. Ne persiraj.")
    return generate_ai_response(prompt)


# --- FUNKCIJE ZA FINALNU FAZU (MISIJA) ---

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
## Izazivaj Kontrolni sistem
* Prepoznaj i razotkri lažne autoritete, lažne poruke i kontrolu.
* Svaki proboj u percepciji oslobađa duhove.
* Budi strpljiv, ali nemoj biti miran.
* Promene se ne dešavaju preko noći.
* Svaka tvoja odluka u SADAŠNJOSTI oblikuje BUDUĆNOST.
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
# 6. WEBHOOK RUTE (Korigovano V3.49)
# ----------------------------------------------------

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if flask.request.headers.get('content-type') == 'application/json':
        json_string = flask.request.get_data().decode('utf-8')
        
        if BOT_TOKEN == "DUMMY:TOKEN_FAIL":
            return "Bot nije konfigurisan. Token nedostaje."
            
        # KOREKCIJA V3.49: Ispravan način parsiranja JSON-a u Update objekat
        try:
             update = telebot.types.Update.de_json(json_string) 
        except Exception as e:
             logging.error(f"Greška pri parsiranju JSON-a (de_json): {e}")
             return '' # Vraćamo prazan odgovor da ne bi Telegram ponavljao zahtev
             
        # Proveravamo da li je poruka validna pre obrade
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
# 7. BOT HANDLERI (Sa trajnim stanjem i Logikom Konverzacije)
# ----------------------------------------------------

@bot.message_handler(commands=['start', 'stop', 'zagonetka', 'pokreni'])
def handle_commands(message):
    
    # --- PROVERA VREMENA (V3.48) ---
    # Svi zahtevi, uključujući /start, moraju poštovati vremenski prozor
    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
        return
    # --- KRAJ PROVERE VREMENA ---
    
    chat_id = str(message.chat.id)
    session = Session() 

    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        if message.text == '/start':
            
            is_existing_player = (player is not None)
            
            # Logika inicijalizacije/resetovanja stanja
            if player:
                # Resetujemo sve metrike pre nego što postavimo novo stanje
                player.is_disqualified = False
                player.solved_count = 0 
                player.failed_attempts = 0 
                player.general_conversation_count = 0 
                
            else:
                # Kreiranje novog igraca
                user = message.from_user
                display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
                
                player = PlayerState(
                    chat_id=chat_id, current_riddle=None, solved_count=0, failed_attempts=0,
                    is_disqualified=False, username=display_name, general_conversation_count=0
                )
                session.add(player)
            
            session.commit() # Snimanje (resetovanog) ili novog stanja
            
            # SCENARIO 1: NOVI IGRAČ - PRVO POKRETANJE
            if not is_existing_player:
                player.current_riddle = "INITIAL_WAIT_1" 
                session.commit()
                send_msg(message, INITIAL_QUERY_1) # -> "Da li vidiš poruku?"
                return
            
            # SCENARIO 2: POVRATNIK (UVEK POTVRDA VOLJE)
            else:
                player.current_riddle = "RETURN_CONFIRMATION_QUERY" # NOVO STANJE
                session.commit()
                send_msg(message, RETURN_DISQUALIFIED_QUERY) # -> "Da li zaista nosiš Volju...? Odgovori isključivo DA ili NE."
                return

        elif message.text == '/stop':
            if player and (player.current_riddle or player.current_riddle == "FINAL_MISSION_QUERY"):
                # Prijatelj odustaje tokom aktivne misije/zagonetke
                player.current_riddle = None 
                player.solved_count = 0 
                player.failed_attempts = 0 
                player.general_conversation_count = 0 
                session.commit()
                send_msg(message, RETURN_FAILURE_MESSAGE) # Koristi se ZBOGOM poruka
            elif player and player.is_disqualified:
                send_msg(message, "Arhiva je zatvorena za tebe.")
            else:
                send_msg(message, "Nisi u testu, Prijatelju. Šta zapravo tražiš?")
        
        elif message.text == '/pokreni' or message.text == '/zagonetka':
            
            if not player:
                send_msg(message, "Moraš kucati /start da bi te Dimitrije prepoznao.")
                return
            
            if player.is_disqualified:
                 send_msg(message, "Arhiva je zatvorena za tebe.")
                 return

            if player.current_riddle in ["INITIAL_WAIT_1", "INITIAL_WAIT_2"]:
                 send_msg(message, "Čekam tvoj potvrdan signal! Da li vidiš poruku? Odgovori DA ili NE.")
                 return
            
            if player.current_riddle == "RETURN_CONFIRMATION_QUERY":
                send_msg(message, "Moraš potvrditi svoju Volju sa **DA** ili **NE** pre nego što nastavimo. Tvoje neodgovaranje produžava agoniju.")
                return

            if player.current_riddle in SUB_RIDDLES.values():
                send_msg(message, "Tvoj odgovor na poslednje pitanje još uvek visi u etru. Moraš da nam objasniš svoju suštinu pre nego što nastavimo.")
                return
            elif player.current_riddle == "FINAL_WARNING_QUERY":
                 send_msg(message, "Moraš potvrditi svoju Volju sa **DA** ili **NE** pre nego što nastavimo. Tvoje neodgovaranje produžava agoniju.")
                 return
            elif player.current_riddle is not None:
                # Već je u toku glavna zagonetka, i igrač je ponovo pozvao /zagonetka
                send_msg(message, "Tvoj um je već zauzet. Predaj nam ključ. Odgovori na Pečat pre nego što pozoveš novi.")
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
                f"Primi ovo, Prijatelju. To je **Pečat mudrosti broj {player.solved_count + 1}**:\n\n**{prva_zagonetka}**"
            )
            
    finally:
        session.close() 


@bot.message_handler(func=lambda message: True)
def handle_general_message(message):
    
    # --- PROVERA VREMENA (V3.48) ---
    if not is_game_active():
        send_msg(message, TIME_LIMIT_MESSAGE)
        return
    # --- KRAJ PROVERE VREMENA ---
    
    chat_id = str(message.chat.id)
    korisnikov_tekst = message.text.strip().lower()
    session = Session()

    try:
        player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
        
        if player and player.is_disqualified:
            send_msg(message, "Tišina. Prolaz je zatvoren.")
            return

        if not player:
            # Koristimo default AI odgovor za potpuno neprepoznatog korisnika
            ai_odgovor = generate_ai_response(message.text)
            send_msg(message, ai_odgovor)
            return

        trenutna_zagonetka = player.current_riddle
        ispravan_odgovor = ZAGONETKE.get(trenutna_zagonetka)

        
        # HANDLER 0.5: POTVRDA VOLJE NAKON POVRATKA (/start) 
        if trenutna_zagonetka == "RETURN_CONFIRMATION_QUERY":
            korisnikov_tekst = korisnikov_tekst.lower()
            
            if "da" in korisnikov_tekst:
                # Nastavlja misiju (KOREKCIJA V3.42 - ODMAH DAJEMO ZAGONETKU)
                
                send_msg(message, RETURN_SUCCESS_MESSAGE) # Prvo šaljemo potvrdnu poruku
                
                # Određujemo novu zagonetku
                riddle_keys = list(ZAGONETKE.keys())
                if player.solved_count < len(riddle_keys):
                     prva_zagonetka = riddle_keys[player.solved_count] 
                     
                     player.current_riddle = prva_zagonetka 
                     player.failed_attempts = 0
                     player.general_conversation_count = 0 
                     session.commit()
                     
                     # Šaljemo zagonetku odmah
                     send_msg(message, 
                        f"Primi ovo, Prijatelju. To je **Pečat mudrosti broj {player.solved_count + 1}**:\n\n**{prva_zagonetka}**"
                     )
                     return
                else:
                    # Svi pečati već slomljeni - šaljemo ga na start
                    player.current_riddle = None 
                    session.commit()
                    send_msg(message, "Svi pečati su slomljeni. Finalna Tajna ti je predata. Vrati se sa /start da je testiraš ponovo.")
                    return
                
            elif "ne" in korisnikov_tekst or "odustajem" in korisnikov_tekst:
                # Trajno odustajanje (vraćanje u tišinu)
                player.current_riddle = None 
                player.is_disqualified = True # Zadržavamo diskvalifikaciju do sledeceg /start
                session.commit()
                send_msg(message, RETURN_FAILURE_MESSAGE) # Koristi se ZBOGOM poruka
                return
            
            else:
                player.current_riddle = "RETURN_CONFIRMATION_QUERY" 
                session.commit()
                send_msg(message, "Odgovori isključivo **DA** ili **NE**. Vreme je izgubljeno!")
                return
        

        # HANDLER 0: INICIJALNI DIJALOG - 'DA LI VIDIŠ PORUKU?' (SCENARIO 1)
        if trenutna_zagonetka in ["INITIAL_WAIT_1", "INITIAL_WAIT_2"]:
            
            if "da" in korisnikov_tekst or "vidim" in korisnikov_tekst or "jesam" in korisnikov_tekst or "da vidim" in korisnikov_tekst or "ovde" in korisnikov_tekst:
                
                # Fiksni uvodni tekst (V3.52)
                ai_intro = DRAMATIC_INTRO_MESSAGE
                
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

        
        # HANDLER 1.5: FINALNO UPOZORENJE (DA/NE) 
        if trenutna_zagonetka == "FINAL_WARNING_QUERY":
            
            korisnikov_tekst = korisnikov_tekst.lower()
            
            if "da" in korisnikov_tekst or "nastavljam" in korisnikov_tekst or "zelim" in korisnikov_tekst:
                # Nastavlja misiju
                player.current_riddle = None 
                player.general_conversation_count = 0 
                session.commit()
                send_msg(message, RETURN_SUCCESS_MESSAGE)
                return
                
            elif "ne" in korisnikov_tekst or "odustajem" in korisnikov_tekst or "ne zelim" in korisnikov_tekst:
                # Trajno odustajanje (vraćanje u tišinu)
                player.current_riddle = None 
                player.solved_count = 0 
                player.failed_attempts = 0
                player.general_conversation_count = 0
                session.commit()
                send_msg(message, RETURN_FAILURE_MESSAGE) # Koristi se ZBOGOM poruka
                return
            
            else:
                player.current_riddle = "FINAL_WARNING_QUERY" 
                session.commit()
                send_msg(message, "Vreme je tanko! Odgovori isključivo **DA** ili **NE** pre nego što nastavimo. Tvoje neodgovaranje produžava agoniju.")
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
                keywords_full_success = ["zapecacena", "vosak", "spremnost", "posvecenost", "zatvorena", "istina se ne daje", "volja", "ne cita se", "teret", "zasluzuje", "odgovornost"]
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


        # HANDLER 3.3: OGRANIČENA KONVERZACIJA I ULTIMATIVNO UPOZORENJE 
        
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
        
        # Pomoćna funkcija za generisanje opšteg AI odgovora
        def generate_conversation_response(user_query, current_riddle, solved_count):
            prompt_base = f"Prijatelj ti je postavio pitanje/komentar ('{user_query}'). Trenutno stanje je: Pečat broj {solved_count + 1} ({current_riddle if current_riddle else 'Nema aktivnog Pečata'}). Odgovori mu u V-stilu, teatralno, i preteći, ali **nikada mu ne daj direktan odgovor** na aktivnu zagonetku. Uvek naglašavaj Volju i Teret."
            return generate_ai_response(prompt_base)


        if is_conversation_request:
            
            MAX_CONVERSATION_COUNT = 5 
            
            if player.general_conversation_count >= MAX_CONVERSATION_COUNT:
                # ULTIMATIVNO UPOZORENJE / POGREŠAN ODGOVOR
                
                ultimate_warning_text = (
                    "Vreme je vrednost koju ne smeš rasipati. Tvoja volja je krhka, a tišina te čeka. Moram da znam, Prijatelju: **Da li želiš da nastaviš ili odustaješ?** Odgovori isključivo **DA** ili **NE**."
                )
                
                send_msg(message, ultimate_warning_text)
                
                player.current_riddle = "FINAL_WARNING_QUERY"
                session.commit()
                return

            # Generisanje opšteg odgovora pre ultimativnog upozorenja
            ai_odgovor_base = generate_conversation_response(korisnikov_tekst, trenutna_zagonetka, player.solved_count)
            
            # Ručno dodajemo instrukciju za nastavak: 
            if trenutna_zagonetka in SUB_RIDDLES.values():
                ai_odgovor = ai_odgovor_base + "\n\n**Prijatelju, odgovori na to Potpitanje! Vreme je izgubljeno, a Istina nas čeka!**"
            elif trenutna_zagonetka is None:
                 ai_odgovor = ai_odgovor_base + "\n\n**Samo Volja stvara Put. Odmah kucaj /pokreni ili /zagonetka** da nastaviš Teret."
            else:
                 ai_odgovor = ai_odgovor_base + "\n\n**Samo Volja stvara Put. Odmah kucaj /zagonetka** da nastaviš Teret."

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
                    
                # Ručno dodajemo korigovanu instrukciju za nastavak:
                send_msg(message, ai_odgovor + "\n\n**Prijatelju, odgovori na to Potpitanje! Vreme je izgubljeno, a Istina nas čeka!**")
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
                send_msg(message, "**ISTINA JE OTKRIVENA!** Ti si dostojan, Prijatelju! Poslednji pečat je slomljen. Finalna Tajna ti pripada.")
                
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
                    
                    player.failed_attempts += 1 # Računamo prvi neuspešni pokušaj u ovom posebnom slučaju
                    session.commit()
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
                # ODUSTAJANJE ZBOG PREVIŠE NEUSPEHA
                send_msg(message, RETURN_FAILURE_MESSAGE) # Koristi se ZBOGOM poruka
                
                player.current_riddle = None
                player.solved_count = 0 
                player.failed_attempts = 0
                player.is_disqualified = False 
                session.commit()
                
            else:
                send_msg(message, "Gledaš, ali ne vidiš, Prijatelju. Ne tražim tvoje znanje, već tvoju suštinu. Vrati se rečima. Pokušaj ponovo, ili kucaj /stop da odustaneš od Tajne.")

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
