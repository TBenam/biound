"""
BIOUND PRO — Module Email Crawler
==================================
Visite les sites web des leads OSM/FB et extrait les adresses email
visibles dans le HTML (mailto:, texte brut, pages contact).

Complètement gratuit — utilise uniquement requests + BeautifulSoup.
"""

import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time

# ============================================================
# CONFIG
# ============================================================

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
}

REQUEST_TIMEOUT = 10   # secondes
MAX_PAGES_PER_SITE = 3  # page d'accueil + /contact + /about max

# Regex email robuste — capture les formats courants
EMAIL_REGEX = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7}\b'
)

# Emails à ignorer (faux positifs fréquents)
EMAIL_BLACKLIST = {
    'example@example.com', 'test@test.com', 'email@email.com',
    'noreply@', 'no-reply@', 'wordpress@', 'woocommerce@',
    'support@sentry.io', 'abuse@', 'postmaster@', 'webmaster@',
}

# Pages contact à essayer sur chaque site
CONTACT_PATHS = [
    '/contact', '/contact-us', '/contactez-nous', '/nous-contacter',
    '/about', '/a-propos', '/qui-sommes-nous',
    '/info', '/informations',
]

# ============================================================
# FONCTIONS UTILITAIRES
# ============================================================

def normalize_url(url: str) -> str:
    """Assure que l'URL a un schéma HTTP(S)."""
    if not url:
        return ''
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

def is_valid_email(email: str) -> bool:
    """Filtre les faux positifs et emails génériques."""
    email = email.lower()
    for blacklisted in EMAIL_BLACKLIST:
        if blacklisted in email:
            return False
    # Rejeter les extensions suspectes (fichiers, images...)
    domain = email.split('@')[-1]
    if any(domain.endswith(ext) for ext in ['.png', '.jpg', '.gif', '.css', '.js']):
        return False
    return True

def extract_emails_from_html(html: str) -> set:
    """Extrait tous les emails valides d'un contenu HTML."""
    emails = set()

    # 1. Depuis les balises mailto:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.find_all('a', href=True):
        href = tag['href']
        if href.startswith('mailto:'):
            email = href.replace('mailto:', '').split('?')[0].strip()
            if EMAIL_REGEX.match(email) and is_valid_email(email):
                emails.add(email.lower())

    # 2. Depuis le texte brut du HTML
    text = soup.get_text()
    for match in EMAIL_REGEX.finditer(text):
        email = match.group().lower()
        if is_valid_email(email):
            emails.add(email)

    return emails

def fetch_page(url: str) -> str | None:
    """Télécharge une page web. Retourne le HTML ou None si erreur."""
    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )
        # On accepte seulement le HTML
        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' not in content_type:
            return None
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.SSLError:
        # Retry sans vérification SSL (sites avec certif expiré)
        try:
            resp = requests.get(
                url, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                allow_redirects=True, verify=False
            )
            return resp.text if resp.ok else None
        except Exception:
            return None
    except Exception:
        return None

# ============================================================
# FONCTION PRINCIPALE
# ============================================================

def crawl_website(website_url: str) -> dict:
    """
    Visite un site web et extrait les emails trouvés.

    Args:
        website_url: URL du site (ex: "https://monrestaurant.cm")

    Returns:
        {
            'emails': ['contact@monrestaurant.cm'],
            'pages_checked': 3,
            'found': True
        }
    """
    url = normalize_url(website_url)
    if not url:
        return {'emails': [], 'pages_checked': 0, 'found': False}

    base_domain = urlparse(url).netloc
    all_emails = set()
    pages_checked = 0

    # Page d'accueil
    html = fetch_page(url)
    if html:
        pages_checked += 1
        all_emails.update(extract_emails_from_html(html))

    # Si déjà trouvé sur la home, on s'arrête
    if all_emails:
        return {
            'emails': sorted(all_emails),
            'pages_checked': pages_checked,
            'found': True
        }

    # Pages contact / about
    for path in CONTACT_PATHS:
        if pages_checked >= MAX_PAGES_PER_SITE:
            break

        contact_url = f"https://{base_domain}{path}"
        html = fetch_page(contact_url)
        if not html:
            # Retry en HTTP
            contact_url = f"http://{base_domain}{path}"
            html = fetch_page(contact_url)

        if html:
            pages_checked += 1
            found = extract_emails_from_html(html)
            all_emails.update(found)
            if found:
                break  # On a ce qu'on cherche

        time.sleep(0.3)  # Politesse

    return {
        'emails': sorted(all_emails),
        'pages_checked': pages_checked,
        'found': len(all_emails) > 0
    }

def crawl_leads_batch(leads: list, on_result=None) -> list:
    """
    Crawle une liste de leads qui ont un site web.
    Appelle on_result(number, result) à chaque résultat si fourni.

    Args:
        leads: liste de dicts avec 'number' et 'website'
        on_result: callback optionnel pour streaming

    Returns:
        Liste de dicts {'number', 'emails', 'found'}
    """
    results = []
    crawlable = [l for l in leads if l.get('website')]

    for lead in crawlable:
        number = lead.get('number', '')
        website = lead.get('website', '')

        result = crawl_website(website)
        entry = {
            'number': number,
            'website': website,
            'emails': result['emails'],
            'found': result['found'],
            'pages_checked': result['pages_checked']
        }
        results.append(entry)

        if on_result:
            on_result(number, entry)

        # Délai entre les sites pour ne pas se faire bloquer
        time.sleep(1)

    return results