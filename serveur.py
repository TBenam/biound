import time
import json
import platform
import os
import requests
# import webbrowser
# import pyautogui  # DÉSACTIVÉ POUR LE SAAS (Crash sur VPS sans écran)
from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory
from flask_cors import CORS
from colorama import init, Fore, Style
from dotenv import load_dotenv
import database
from emailcrawler import crawl_website, crawl_leads_batch
import annuaires
import scheduler as sched

# Charger les variables d'environnement
load_dotenv()

# ============================================================
# BIOUND PRO - Sawa Tech Edition
# Backend v5.2 — Bugs fixes + Email Crawler
# ============================================================

init(autoreset=True)
app = Flask(__name__)
CORS(app)

# ============================================================
# CONFIGURATION
# ============================================================
META_ACCESS_TOKEN = os.environ.get("META_TOKEN", "COLLE_TON_TOKEN_ICI")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY")

# ============================================================
# ROUTES STATIQUES
# ============================================================

@app.route('/')
def landing():
    return send_from_directory('.', 'landing.html')

@app.route('/app')
def index_app():
    return send_from_directory('.', 'index.html')

# ============================================================
# MODULE 1 : META AD LIBRARY API
# ============================================================

COUNTRY_KEYWORDS = {
    "CM": ["douala", "yaounde", "cameroun", "livraison", "promotion", "vente"],
    "CI": ["abidjan", "cocody", "plateau", "livraison", "promotion", "cote ivoire"],
    "SN": ["dakar", "senegal", "livraison", "promotion", "vente"],
    "FR": ["paris", "lyon", "marseille", "livraison", "promo", "france"],
    "BE": ["bruxelles", "liege", "belgique", "livraison", "promo"],
    "CA": ["montreal", "quebec", "canada", "livraison", "promo"],
    "CH": ["geneve", "lausanne", "zurich", "suisse", "promo"],
    "CD": ["kinshasa", "lubumbashi", "congo", "livraison", "vente"],
    "GA": ["libreville", "gabon", "livraison", "vente"],
    "TG": ["lome", "togo", "livraison", "vente"],
    "BJ": ["cotonou", "benin", "livraison", "vente"],
    "ML": ["bamako", "mali", "livraison", "vente"],
}

def fetch_meta_ads(country: str, keyword: str, limit: int = 25) -> list:
    url = "https://graph.facebook.com/v19.0/ads_archive"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "ad_reached_countries": f'["{country}"]',
        "search_terms": keyword,
        "ad_active_status": "ACTIVE",
        "fields": "ad_creative_body,ad_creative_link_caption,ad_creative_link_description,page_name,ad_snapshot_url",
        "limit": limit
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for ad in data.get("data", []):
            parts = [
                ad.get("page_name", ""),
                ad.get("ad_creative_body", ""),
                ad.get("ad_creative_link_caption", ""),
                ad.get("ad_creative_link_description", ""),
            ]
            full_text = " | ".join(p for p in parts if p)
            if full_text.strip():
                results.append(full_text)
        next_page = data.get("paging", {}).get("next")
        if next_page and len(results) < 100:
            try:
                resp2 = requests.get(next_page, timeout=20)
                for ad in resp2.json().get("data", []):
                    parts = [ad.get("page_name",""), ad.get("ad_creative_body",""),
                             ad.get("ad_creative_link_caption",""), ad.get("ad_creative_link_description","")]
                    full_text = " | ".join(p for p in parts if p)
                    if full_text.strip():
                        results.append(full_text)
            except Exception:
                pass
        return results
    except requests.exceptions.HTTPError as e:
        print(Fore.RED + f"❌ Meta API HTTP Error ({e.response.status_code}): {e.response.text[:200]}")
        return []
    except Exception as e:
        print(Fore.RED + f"❌ Meta API Error: {e}")
        return []


@app.route('/auto_hunt', methods=['POST'])
def auto_hunt():
    data    = request.json
    country = data.get('country', 'CM')
    keywords = COUNTRY_KEYWORDS.get(country, COUNTRY_KEYWORDS["CM"])

    def generate():
        if META_ACCESS_TOKEN == "COLLE_TON_TOKEN_ICI":
            yield json.dumps({"status": "error", "message": "Token Meta manquant."}) + "\n"
            return

        total_found = 0
        seen_texts  = set()

        for keyword in keywords:
            print(Fore.YELLOW + f"🔍 [META API] Keyword: '{keyword}' | Pays: {country}")
            ads = fetch_meta_ads(country, keyword)
            print(Fore.CYAN + f"   → {len(ads)} annonces récupérées")

            for ad_text in ads:
                key = ad_text[:80]
                if key in seen_texts:
                    continue
                seen_texts.add(key)
                yield json.dumps({
                    "status": "lead", "text": ad_text,
                    "source": "meta_api", "keyword": keyword
                }) + "\n"
                total_found += 1

            time.sleep(1)

        print(Fore.GREEN + f"✅ Session terminée. {total_found} annonces uniques.")
        yield json.dumps({"status": "done", "total": total_found}) + "\n"

    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')


# ============================================================
# MODULE 2 : OSM HUNTER
# ============================================================

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

OSM_CATEGORY_TAGS = {
    "restaurant": [('amenity','restaurant'),('amenity','fast_food'),('amenity','cafe'),('amenity','bar')],
    "beaute":     [('shop','hairdresser'),('shop','beauty'),('leisure','spa')],
    "sante":      [('amenity','clinic'),('amenity','hospital'),('amenity','pharmacy'),('amenity','dentist'),('amenity','doctors')],
    "education":  [('amenity','school'),('amenity','university'),('amenity','college'),('amenity','training')],
    "automobile": [('shop','car'),('shop','car_repair'),('amenity','car_rental')],
    "ecommerce":  [('shop','clothes'),('shop','electronics'),('shop','supermarket'),('shop','convenience')],
    "immobilier": [('office','estate_agent'),('office','company')],
}

CITY_BBOX = {
    # Afrique
    "douala":      {"lat": 4.0511,  "lon": 9.7679,   "radius": 8000},
    "yaounde":     {"lat": 3.8480,  "lon": 11.5021,  "radius": 7000},
    "abidjan":     {"lat": 5.3599,  "lon": -4.0082,  "radius": 9000},
    "dakar":       {"lat": 14.7167, "lon": -17.4677, "radius": 7000},
    "kinshasa":    {"lat": -4.4419, "lon": 15.2663,  "radius": 10000},
    "lubumbashi":  {"lat": -11.6876,"lon": 27.5026,  "radius": 7000},
    "libreville":  {"lat": 0.4162,  "lon": 9.4673,   "radius": 6000},
    "lome":        {"lat": 6.1256,  "lon": 1.2254,   "radius": 6000},
    "cotonou":     {"lat": 6.3654,  "lon": 2.4183,   "radius": 6000},
    "bamako":      {"lat": 12.6392, "lon": -8.0029,  "radius": 7000},
    # Europe
    "paris":       {"lat": 48.8566, "lon": 2.3522,   "radius": 12000},
    "lyon":        {"lat": 45.7640, "lon": 4.8357,   "radius": 8000},
    "marseille":   {"lat": 43.2965, "lon": 5.3698,   "radius": 8000},
    "bruxelles":   {"lat": 50.8503, "lon": 4.3517,   "radius": 8000},
    "liege":       {"lat": 50.6326, "lon": 5.5797,   "radius": 6000},
    "geneve":      {"lat": 46.2044, "lon": 6.1432,   "radius": 6000},
    "lausanne":    {"lat": 46.5197, "lon": 6.6323,   "radius": 5000},
    # Amérique
    "montreal":    {"lat": 45.5017, "lon": -73.5673, "radius": 10000},
    "quebec":      {"lat": 46.8139, "lon": -71.2080, "radius": 7000},
}

CITIES_BY_COUNTRY = {
    "CM": ["douala", "yaounde"],
    "CI": ["abidjan"],
    "SN": ["dakar"],
    "FR": ["paris", "lyon", "marseille"],
    "BE": ["bruxelles", "liege"],
    "CA": ["montreal", "quebec"],
    "CH": ["geneve", "lausanne"],
    "CD": ["kinshasa", "lubumbashi"],
    "GA": ["libreville"],
    "TG": ["lome"],
    "BJ": ["cotonou"],
    "ML": ["bamako"],
}

def build_overpass_query(key, value, lat, lon, radius):
    return f"""
[out:json][timeout:25];
(
  node["{key}"="{value}"](around:{radius},{lat},{lon});
  way["{key}"="{value}"](around:{radius},{lat},{lon});
);
out body;
"""

def parse_osm_element(el, category, city):
    tags = el.get("tags", {})
    name = tags.get("name") or tags.get("name:fr") or tags.get("brand")
    if not name:
        return None
    phone = (tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile") or "").replace(" ","").replace("-","")
    website  = tags.get("website") or tags.get("contact:website") or ""
    address_parts = [tags.get("addr:housenumber",""), tags.get("addr:street",""), tags.get("addr:city","")]
    address  = " ".join(p for p in address_parts if p).strip() or city.capitalize()
    text     = " | ".join(p for p in [name, phone, address, website] if p)
    return {"name": name, "phone": phone, "website": website, "address": address, "city": city, "category": category, "source": "osm", "text": text}

def search_osm(key, value, city):
    coords = CITY_BBOX.get(city, CITY_BBOX["douala"])
    query  = build_overpass_query(key, value, coords["lat"], coords["lon"], coords["radius"])
    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=30, headers={"User-Agent": "BioundPro/5.2"})
        resp.raise_for_status()
        elements   = resp.json().get("elements", [])
        leads      = []
        seen_names = set()
        for el in elements:
            lead = parse_osm_element(el, f"{key}={value}", city)
            if lead and lead["name"] not in seen_names:
                seen_names.add(lead["name"])
                leads.append(lead)
        return leads
    except requests.exceptions.Timeout:
        print(Fore.YELLOW + f"⚠️  Overpass timeout {key}={value} à {city}")
        return []
    except Exception as e:
        print(Fore.RED + f"❌ Overpass error: {e}")
        return []


@app.route('/maps_hunt', methods=['POST'])
def maps_hunt():
    data    = request.json
    country = data.get('country', 'CM')
    cities  = CITIES_BY_COUNTRY.get(country, ["douala"])

    def generate():
        total = 0
        for city in cities:
            for category, tag_list in OSM_CATEGORY_TAGS.items():
                for (key, value) in tag_list:
                    print(Fore.YELLOW + f"🗺️  [OSM] {key}={value} → {city}")
                    leads = search_osm(key, value, city)
                    for lead in leads:
                        yield json.dumps({
                            "status": "lead", "source": "osm",
                            "text": lead["text"], "name": lead["name"],
                            "phone": lead["phone"], "website": lead["website"],
                            "address": lead["address"], "city": lead["city"],
                            "category": category,
                        }) + "\n"
                        total += 1
                    time.sleep(1.2)
        print(Fore.GREEN + f"✅ OSM Hunt terminé — {total} établissements.")
        yield json.dumps({"status": "done", "total": total}) + "\n"

    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')


# ============================================================
# MODULE 2b : ANNUAIRE HUNTER
# ============================================================

@app.route('/annuaire_hunt', methods=['POST'])
def annuaire_hunt():
    data    = request.json
    country = data.get('country', '')

    def generate():
        total = 0
        print(Fore.YELLOW + f"📔 [ANNUAIRE] Lancement pour {country or 'Tous'}...")
        # Message de "warmup" pour éviter que le navigateur ne pense que c'est bloqué
        yield json.dumps({"status": "progress", "message": f"Initialisation recherche {country}..."}) + "\n"
        
        for lead in annuaires.search_annuaire(country):
            yield json.dumps({
                "status": "lead", "source": "annuaire",
                "text": lead["text"], "name": lead["name"],
                "phone": lead["phone"], "website": lead["website"],
                "address": lead.get("address", ""), "city": lead.get("city", ""),
                "category": lead.get("category", "autre"),
            }) + "\n"
            total += 1
        print(Fore.GREEN + f"✅ Annuaire terminé — {total} fiches.")
        yield json.dumps({"status": "done", "total": total}) + "\n"

    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')


# ============================================================
# MODULE 3 : ANALYSE IA — Groq (LLaMA 3) + Gemini Fallback
# ============================================================

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

ANALYSIS_PROMPT = """Tu es un expert en prospection B2B pour l'espace francophone (Afrique, Europe, Canada).

Analyse cette annonce ou fiche business : "{context}"

Réponds EXCLUSIVEMENT en JSON valide, sans markdown, sans backticks, avec exactement ces clés :
{{
  "business_category": "restaurant | ecommerce | immobilier | automobile | beaute | sante | education | autre",
  "service_needed": "site_web | identite_visuelle | call_center | reseaux_sociaux | publicite_meta | autre",
  "score": <entier de 1 à 10 selon le potentiel commercial>,
  "digital_score": <entier de 1 à 10 évaluant la maturité digitale actuelle du business : 1=aucune présence en ligne, 10=très bien digitalisé>,
  "besoin": "<résumé du besoin principal en 6 mots max>",
  "accroche": "<message WhatsApp de prospection, 1 phrase courte, naturelle, personnalisée>"
}}"""

FALLBACK_RESPONSE = {
    "business_category": "autre", "service_needed": "site_web",
    "score": 5, "digital_score": 3, "besoin": "Analyse indisponible",
    "accroche": "Bonjour, j'ai vu votre activité. On peut vous aider à développer votre présence en ligne ?"
}

def call_groq(context: str) -> dict:
    """Appelle Groq API (LLaMA 3) pour analyser un lead."""
    prompt = ANALYSIS_PROMPT.format(context=context[:500])
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 300,
        "response_format": {"type": "json_object"}
    }
    resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(clean)

def call_gemini(context: str) -> dict:
    """Appelle Gemini Flash (fallback si Groq non configuré)."""
    prompt  = ANALYSIS_PROMPT.format(context=context[:500])
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 300, "responseMimeType": "application/json"}
    }
    resp  = requests.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}", json=payload, timeout=15)
    resp.raise_for_status()
    raw   = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(clean)

def call_ai(context: str) -> dict:
    """Point d'entrée unique : utilise Groq si clé disponible, sinon Gemini."""
    if GROQ_API_KEY:
        return call_groq(context)
    elif GEMINI_API_KEY:
        return call_gemini(context)
    else:
        raise ValueError("Aucune clé IA configurée (GROQ_API_KEY ou GEMINI_API_KEY manquante)")


@app.route('/analyze_lead', methods=['POST'])
def analyze_lead():
    data    = request.json
    context = data.get('context', '')
    number  = data.get('number', '')

    try:
        result = call_ai(context)
        provider = "Groq" if GROQ_API_KEY else "Gemini"
        print(Fore.GREEN + f"✅ {provider} → {result.get('business_category')} | score {result.get('score')}/10")

        # BUG FIX #4 : on sauvegarde dans ai_accroche (colonne dédiée) ET product
        if number:
            database.update_lead(number, {
                'ai_score':       result.get('score', 0),
                'digital_score':  result.get('digital_score', 0),
                'category':       result.get('business_category', 'autre'),
                'service_needed': result.get('service_needed', ''),
                'besoin':         result.get('besoin', ''),
                'ai_accroche':    result.get('accroche', ''),
                'product':        result.get('accroche', ''),
                'analyzed':       True
            })
        return jsonify(result)

    except ValueError as e:
        print(Fore.YELLOW + f"⚠️  {e}")
        return jsonify({**FALLBACK_RESPONSE, "besoin": "Clé Gemini manquante"}), 200
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code
        msg  = {429: "Quota Gemini atteint"}.get(code, f"Gemini HTTP {code}")
        print(Fore.YELLOW + f"⚠️  {msg}")
        return jsonify({**FALLBACK_RESPONSE, "besoin": msg}), 200
    except (json.JSONDecodeError, KeyError) as e:
        print(Fore.RED + f"❌ Parsing Gemini : {e}")
        return jsonify(FALLBACK_RESPONSE), 200
    except Exception as e:
        print(Fore.RED + f"❌ Erreur inattendue : {e}")
        return jsonify(FALLBACK_RESPONSE), 200


# ============================================================
# MODULE 4 : EMAIL CRAWLER
# ============================================================

@app.route('/crawl_email', methods=['POST'])
def crawl_email_single():
    """
    Crawle le site web d'un lead unique pour en extraire les emails.
    Appelé depuis le bouton sur la card.
    """
    data    = request.json
    number  = data.get('number', '')
    website = data.get('website', '')

    if not website:
        return jsonify({"emails": [], "found": False, "message": "Pas de site web renseigné"}), 400

    print(Fore.YELLOW + f"📧 [CRAWLER] {website}")
    result = crawl_website(website)

    # Sauvegarder en base si emails trouvés
    if result['found'] and number:
        database.update_lead(number, {'email': ','.join(result['emails'])})
        print(Fore.GREEN + f"   → {result['emails']}")
    else:
        print(Fore.CYAN + f"   → Aucun email trouvé ({result['pages_checked']} pages vérifiées)")

    return jsonify(result)


@app.route('/crawl_emails_batch', methods=['POST'])
def crawl_emails_batch():
    """
    Crawle tous les leads ayant un site web, en streaming NDJSON.
    Permet de lancer une session de crawl groupée.
    """
    data    = request.json
    country = data.get('country', 'CM')
    leads   = database.get_all_leads(country=country)

    # Ne crawle que ceux avec un site web et sans email déjà renseigné
    to_crawl = [l for l in leads if l.get('website') and not l.get('email')]

    def generate():
        if not to_crawl:
            yield json.dumps({"status": "done", "total": 0,
                              "message": "Aucun site web à crawler."}) + "\n"
            return

        found_count = 0
        print(Fore.YELLOW + f"📧 [CRAWLER BATCH] {len(to_crawl)} sites à crawler...")

        for lead in to_crawl:
            number  = lead['number']
            website = lead['website']
            print(Fore.YELLOW + f"   → {website}")

            result = crawl_website(website)

            if result['found']:
                email_str = ','.join(result['emails'])
                database.update_lead(number, {'email': email_str})
                found_count += 1
                print(Fore.GREEN + f"   ✅ {result['emails']}")
            else:
                print(Fore.CYAN + f"   — Aucun email ({result['pages_checked']} pages)")

            yield json.dumps({
                "status":   "result",
                "number":   number,
                "website":  website,
                "emails":   result['emails'],
                "found":    result['found'],
            }) + "\n"

            time.sleep(1)

        print(Fore.GREEN + f"✅ Crawl terminé — emails trouvés sur {found_count}/{len(to_crawl)} sites.")
        yield json.dumps({"status": "done", "total": len(to_crawl), "found": found_count}) + "\n"

    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')


# ============================================================
# MODULE 5 : WHATSAPP AUTO
# ============================================================

@app.route('/send_whatsapp', methods=['POST'])
def send_whatsapp():
    data    = request.json
    leads   = data.get('leads', [])
    hotkey  = 'command' if platform.system() == 'Darwin' else 'ctrl'

    for index, lead in enumerate(leads):
        number = lead.get('number')

        # --- DÉSACTIVÉ POUR LE SAAS ---
        # L'automatisation WhatsApp se fait désormais manuellement ou par une API officielle,
        # car un VPS n'a pas d'écran pour utiliser pyautogui.
        # webbrowser.open(lead['wa_link'])
        # time.sleep(8)
        # pyautogui.press('enter')
        # time.sleep(2)
        # pyautogui.hotkey(hotkey, 'w')

        # BUG FIX #5 : statut mis à jour APRÈS l'envoi
        if number:
            database.update_lead(number, {'status': 'Contacté'})
            database.add_interaction(number, 'WhatsApp', 'Premier contact envoyé via Biound Auto')

        if index < len(leads) - 1:
            time.sleep(10)

    return jsonify({"status": "success", "message": f"{len(leads)} messages envoyés."})


# ============================================================
# MODULE 4b : CRM & INTERACTIONS
# ============================================================

@app.route('/api/interactions', methods=['POST'])
def add_interaction_api():
    data = request.json
    number = data.get('number')
    it_type = data.get('type')
    note = data.get('note', '')
    if not number or not it_type:
        return jsonify({"status": "error", "message": "Données manquantes"}), 400
    database.add_interaction(number, it_type, note)
    return jsonify({"status": "success"})

@app.route('/api/interactions/<number>', methods=['GET'])
def get_interactions_api(number):
    interactions = database.get_interactions(number)
    return jsonify(interactions)


# ============================================================
# MODULE 6 : API LEADS
# ============================================================

@app.route('/api/leads', methods=['GET'])
def get_leads():
    # BUG FIX #3 : filtre pays depuis le query param
    country = request.args.get('country')
    category = request.args.get('category', 'all')
    leads   = database.get_all_leads(country=country, category=category)
    return jsonify(leads)

@app.route('/api/leads', methods=['POST'])
def add_lead_api():
    result = database.add_lead(request.json)
    return jsonify(result)

@app.route('/api/leads/update', methods=['POST'])
def update_lead_api():
    data   = request.json
    number = data.get('number')
    if not number:
        return jsonify({"status": "error", "message": "Numéro requis"}), 400
    updated = database.update_lead(number, data)
    if updated:
        return jsonify({"status": "success", "lead": updated})
    return jsonify({"status": "error", "message": "Lead non trouvé"}), 404

@app.route('/api/leads/delete', methods=['POST'])
def delete_lead_api():
    number = request.json.get('number')
    if not number:
        return jsonify({"status": "error", "message": "Numéro requis"}), 400
    success = database.delete_lead(number)
    if success:
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Lead non trouvé"}), 404

@app.route('/api/stats', methods=['GET'])
def get_stats():
    country = request.args.get('country', 'CM')
    return jsonify(database.get_pipeline_stats(country))

@app.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    country = request.args.get('country', 'CM')
    return jsonify(database.get_dashboard_stats(country))


# ============================================================
# MODULE 7 : SCHEDULER API
# ============================================================

@app.route('/api/scheduler', methods=['GET'])
def scheduler_status():
    """Retourne l'état du scheduler et l'heure du prochain lancement."""
    return jsonify(sched.get_scheduler_status())

@app.route('/api/scheduler/trigger', methods=['POST'])
def scheduler_trigger():
    """Force un lancement immédiat de la session de prospection (pour tests)."""
    sched.trigger_now()
    return jsonify({'status': 'ok', 'message': 'Session lancée en arrière-plan'})

@app.route('/api/scheduler/config', methods=['POST'])
def scheduler_config():
    """Met à jour l'heure et les pays du scheduler à chaud."""
    data    = request.json
    hour    = data.get('hour')
    minute  = data.get('minute', 0)
    countries = data.get('countries')

    if hour is not None:
        sched.SCHEDULE_HOUR   = int(hour)
        sched.SCHEDULE_MINUTE = int(minute)

    if countries:
        sched.AUTO_COUNTRIES = countries

    # Replanifier le job avec les nouvelles valeurs
    from apscheduler.triggers.cron import CronTrigger
    job = sched._scheduler.get_job('daily_prospection')
    if job:
        job.reschedule(trigger=CronTrigger(
            hour=sched.SCHEDULE_HOUR,
            minute=sched.SCHEDULE_MINUTE
        ))

    return jsonify(sched.get_scheduler_status())


# ============================================================
# DÉMARRAGE
# ============================================================

if __name__ == '__main__':
    database.init_db()
    sched.init_scheduler()

    import atexit
    atexit.register(sched.shutdown_scheduler)

    print(Fore.GREEN + """
╔══════════════════════════════════════╗
║   BIOUND PRO v5.3 - SAWA TECH       ║
║   Meta + OSM + Gemini + Scheduler   ║
╚══════════════════════════════════════╝
    """)

    if META_ACCESS_TOKEN == "COLLE_TON_TOKEN_ICI":
        print(Fore.YELLOW + "⚠️  META_TOKEN non configuré → FB Ads Hunter désactivé")
        print(Fore.CYAN   + "   → https://developers.facebook.com/\n")

    print(Fore.GREEN + "✅ OSM Hunter actif — Overpass API, zéro clé requise")

    if GEMINI_API_KEY == "COLLE_TA_CLE_GEMINI_ICI":
        print(Fore.YELLOW + "⚠️  GEMINI_API_KEY manquante → Analyse IA désactivée")
    else:
        print(Fore.GREEN + "✅ Gemini Flash 2.0 actif — ~1s/analyse, 1500 req/jour gratuits")

    print(Fore.GREEN + "✅ Email Crawler actif — BeautifulSoup, zéro clé requise")
    print(Fore.GREEN + f"✅ Scheduler actif — lancement quotidien à {sched.SCHEDULE_HOUR:02d}:{sched.SCHEDULE_MINUTE:02d}\n")

    app.run(host='0.0.0.0', port=5000, threaded=True, debug=False)