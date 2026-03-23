"""
annuaires.py — Module de scraping d'annuaires web francophones
Biound Pro

Scrape les PagesJaunes et annuaires similaires pour extraire des leads :
nom, téléphone, site web, adresse, catégorie.
"""

import requests
from bs4 import BeautifulSoup
import time
import re

# User-Agent réaliste pour éviter le blocage
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ============================================================
# PAGESJAUNES.FR — France
# ============================================================

def search_pagesjaunes_fr(query: str, location: str, max_pages: int = 3):
    """
    Recherche sur PagesJaunes.fr (Générateur)
    """
    base_url = "https://www.pagesjaunes.fr/annuaire/chercherlespros"

    for page in range(1, max_pages + 1):
        params = {
            "quoiqui": query,
            "ou": location,
            "page": page,
        }
        try:
            resp = requests.get(base_url, params=params, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                print(f"  ⚠️ PagesJaunes HTTP {resp.status_code} (page {page})")
                break
            
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Cherche les fiches business
            listings = soup.select(".bi-bloc, .bi-content, .bi-denomination")
            if not listings and page == 1:
                listings = soup.select("[data-pjseo], .pj-bloc")
            
            if not listings:
                break
            
            for listing in listings:
                lead = _parse_pj_listing(listing, query, location)
                if lead and lead["name"]:
                    yield lead
            
            time.sleep(2)
            
        except Exception as e:
            print(f"  ❌ PagesJaunes error: {e}")
            break


def _parse_pj_listing(listing, query: str, location: str) -> dict:
    """Parse une fiche PagesJaunes."""
    name = ""
    phone = ""
    website = ""
    address = ""
    
    # Nom
    name_el = listing.select_one(".bi-denomination, .denomination-links a, h3 a, .bi-name")
    if name_el:
        name = name_el.get_text(strip=True)
    
    # Téléphone
    phone_el = listing.select_one(".bi-phone .tel, .coord-numero, [data-pjphone], .phone-number")
    if phone_el:
        phone = re.sub(r'\D', '', phone_el.get_text(strip=True))
    
    # Site web
    link_el = listing.select_one(".bi-website a, .coord-url a, a[data-pjsite]")
    if link_el:
        website = link_el.get("href", "")
    
    # Adresse
    addr_el = listing.select_one(".bi-address, .bi-adresse, .coord-adresse")
    if addr_el:
        address = addr_el.get_text(" ", strip=True)
    
    if not name:
        return None
    
    return {
        "name": name,
        "phone": phone,
        "website": website,
        "address": address,
        "city": location.capitalize(),
        "category": _guess_category(query),
        "source": "annuaire",
        "text": f"{name} | {phone} | {address} | {website}",
    }


# ============================================================
# ANNUAIRE GÉNÉRIQUE — Scraping simple
# ============================================================

def search_annuaire_generic(query: str, location: str, country: str = "FR"):
    """
    Recherche générique via DuckDuckGo Lite (Générateur)
    """
    url = "https://lite.duckduckgo.com/lite"
    params = {"q": f"{query} {location} téléphone site", "kl": f"{country.lower()}-fr"}
    
    try:
        resp = requests.post(url, data=params, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Extraire les résultats
        results = soup.select("a.result-link, td a[href^='http']")
        
        for link in results[:15]:
            href = link.get("href", "")
            title = link.get_text(strip=True)
            if href and title and "duckduckgo" not in href:
                yield {
                    "name": title[:60],
                    "phone": "",
                    "website": href,
                    "address": "",
                    "city": location.capitalize(),
                    "category": _guess_category(query),
                    "source": "annuaire",
                    "text": f"{title} | {href}",
                }
        
    except Exception as e:
        print(f"  ❌ Annuaire générique error: {e}")


# ============================================================
# DISPATCHER — Choisit l'annuaire selon le pays
# ============================================================

ANNUAIRE_QUERIES = {
    "restaurant":  ["restaurant", "pizzeria", "boulangerie"],
    "beaute":      ["salon coiffure", "institut beaute", "spa"],
    "sante":       ["medecin", "dentiste", "pharmacie", "clinique"],
    "automobile":  ["garage automobile", "concessionnaire auto"],
    "immobilier":  ["agence immobiliere", "promoteur immobilier"],
    "ecommerce":   ["boutique vetements", "magasin electronique"],
    "education":   ["ecole privee", "centre formation"],
}

ANNUAIRE_CITIES = {
    "FR": ["paris", "lyon", "marseille"],
    "BE": ["bruxelles", "liege"],
    "CA": ["montreal", "quebec"],
    "CH": ["geneve", "lausanne"],
    "CM": ["douala", "yaounde"],
    "CI": ["abidjan", "bouake"],
    "SN": ["dakar", "thies"],
    "CD": ["kinshasa", "lubumbashi"],
    "GA": ["libreville"],
    "TG": ["lome"],
    "BJ": ["cotonou"],
    "ML": ["bamako"],
}

def search_annuaire(country: str, categories: list = None):
    """
    Point d'entrée principal (Générateur)
    """
    cities = ANNUAIRE_CITIES.get(country, ["Douala"]) # Fallback par défaut
    
    if categories is None:
        categories = list(ANNUAIRE_QUERIES.keys())
    
    seen_names = set()
    
    for city in cities:
        for cat in categories:
            queries = ANNUAIRE_QUERIES.get(cat, [cat])
            for query in queries[:2]:
                print(f"  📒 [ANNUAIRE] {query} → {city} ({country})")
                
                try:
                    gen = search_pagesjaunes_fr(query, city, max_pages=1) if country == "FR" else search_annuaire_generic(query, city, country)
                    
                    for lead in gen:
                        if lead["name"] not in seen_names:
                            seen_names.add(lead["name"])
                            lead["category"] = cat
                            yield lead
                except Exception as e:
                    print(f"  ⚠️  [ANNUAIRE] Erreur pour {query} à {city}: {e}")
                
                time.sleep(1)


# ============================================================
# UTILS
# ============================================================

def _guess_category(query: str) -> str:
    """Devine la catégorie à partir de la requête de recherche."""
    query_lower = query.lower()
    mapping = {
        "restaurant": "restaurant", "pizzeria": "restaurant", "boulangerie": "restaurant",
        "cafe": "restaurant", "traiteur": "restaurant",
        "coiffure": "beaute", "beaute": "beaute", "spa": "beaute", "esthetique": "beaute",
        "medecin": "sante", "dentiste": "sante", "pharmacie": "sante", "clinique": "sante",
        "garage": "automobile", "auto": "automobile", "concessionnaire": "automobile",
        "immobilier": "immobilier", "agence": "immobilier",
        "boutique": "ecommerce", "magasin": "ecommerce", "vetement": "ecommerce",
        "ecole": "education", "formation": "education", "universite": "education",
    }
    for keyword, cat in mapping.items():
        if keyword in query_lower:
            return cat
    return "autre"
