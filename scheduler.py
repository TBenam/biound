"""
BIOUND PRO — Scheduler quotidien
==================================
Lance automatiquement chaque matin :
  1. OSM Hunter  → nouveaux leads par ville/catégorie
  2. Email Crawl → extraction emails sur les sites web trouvés
  3. Analyse IA  → Gemini catégorise et score chaque nouveau lead

Utilise APScheduler (BackgroundScheduler, zéro thread bloquant).
Tous les résultats sont persistés dans leads.db via le module database.
"""

import time
import requests
import threading
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from colorama import Fore, Style

import database

# ============================================================
# CONFIG SCHEDULER
# ============================================================

# Heure de lancement quotidien (format 24h, heure locale)
SCHEDULE_HOUR   = 8   # 08:00
SCHEDULE_MINUTE = 0

# Pays à prospecter automatiquement chaque matin
AUTO_COUNTRIES = ['CM']  # Ajouter 'CI', 'SN' pour multi-pays

# URL du serveur Flask local (le scheduler tourne dans le même process)
BASE_URL = 'http://localhost:5000'

# Délai entre chaque lead analysé pour respecter les quotas Gemini (ms)
ANALYSIS_DELAY_S = 1.2

# ============================================================
# LOG HELPER
# ============================================================

def log(msg, color=Fore.CYAN):
    ts = datetime.now().strftime('%H:%M:%S')
    print(color + f"[SCHEDULER {ts}] {msg}" + Style.RESET_ALL)


# ============================================================
# ÉTAPE 1 : OSM HUNT — collecter de nouveaux leads
# ============================================================

def run_osm_hunt(country: str) -> int:
    """
    Appelle /maps_hunt en streaming et compte les nouveaux leads ajoutés.
    Retourne le nombre de leads nouvellement insérés en base.
    """
    log(f"🗺️  OSM Hunt → {country}", Fore.YELLOW)
    new_leads = 0

    try:
        resp = requests.post(
            f'{BASE_URL}/maps_hunt',
            json={'country': country},
            stream=True,
            timeout=600   # 10 min max pour tout le pays
        )
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            try:
                import json
                data = json.loads(raw_line)
                if data.get('status') == 'lead':
                    new_leads += 1
                elif data.get('status') == 'done':
                    log(f"   ✅ OSM terminé — {data.get('total', 0)} établissements", Fore.GREEN)
            except Exception:
                pass
    except Exception as e:
        log(f"   ❌ OSM Hunt erreur : {e}", Fore.RED)

    return new_leads


# ============================================================
# ÉTAPE 2 : EMAIL CRAWL — enrichir les leads avec un site web
# ============================================================

def run_email_crawl(country: str) -> int:
    """
    Crawle les sites web des leads sans email via /crawl_emails_batch.
    Retourne le nombre d'emails trouvés.
    """
    log(f"📧 Email Crawl → {country}", Fore.YELLOW)
    found = 0

    try:
        resp = requests.post(
            f'{BASE_URL}/crawl_emails_batch',
            json={'country': country},
            stream=True,
            timeout=600
        )
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            try:
                import json
                data = json.loads(raw_line)
                if data.get('status') == 'result' and data.get('found'):
                    found += 1
                elif data.get('status') == 'done':
                    log(f"   ✅ Crawl terminé — emails sur {data.get('found', 0)}/{data.get('total', 0)} sites", Fore.GREEN)
            except Exception:
                pass
    except Exception as e:
        log(f"   ❌ Email Crawl erreur : {e}", Fore.RED)

    return found


# ============================================================
# ÉTAPE 3 : ANALYSE IA — Gemini pour les leads non analysés
# ============================================================

def run_ai_analysis(country: str) -> int:
    """
    Analyse avec Gemini chaque lead non analysé dans la base.
    Retourne le nombre de leads analysés.
    """
    log(f"🤖 Analyse IA → {country}", Fore.YELLOW)
    leads = database.get_all_leads(country=country)
    pending = [l for l in leads if not l.get('analyzed') and l.get('context')]

    if not pending:
        log("   ℹ️  Aucun lead en attente d'analyse.", Fore.CYAN)
        return 0

    log(f"   → {len(pending)} leads à analyser...", Fore.CYAN)
    analyzed = 0

    for lead in pending:
        number  = lead['number']
        context = lead.get('context', '')[:500]

        try:
            import json, requests as req
            r = req.post(
                f'{BASE_URL}/analyze_lead',
                json={'context': context, 'number': number},
                timeout=20
            )
            result = r.json()
            score = result.get('score', 0)
            cat   = result.get('business_category', 'autre')
            log(f"   ✅ {number} → {cat} | score {score}/10", Fore.GREEN)
            analyzed += 1
        except Exception as e:
            log(f"   ⚠️  Analyse échouée pour {number} : {e}", Fore.YELLOW)

        time.sleep(ANALYSIS_DELAY_S)  # Quota Gemini

    return analyzed


# ============================================================
# JOB PRINCIPAL : session de prospection complète
# ============================================================

def daily_prospection_job():
    """
    Session complète de prospection autonome :
    OSM Hunt → Email Crawl → Analyse IA
    Exécutée chaque matin à l'heure configurée.
    """
    start = datetime.now()
    log("=" * 50, Fore.GREEN)
    log(f"🚀 DÉMARRAGE SESSION QUOTIDIENNE — {start.strftime('%d/%m/%Y %H:%M')}", Fore.GREEN)
    log("=" * 50, Fore.GREEN)

    total_new    = 0
    total_emails = 0
    total_ai     = 0

    for country in AUTO_COUNTRIES:
        log(f"\n📍 PAYS : {country}", Fore.CYAN)

        # 1. Collecter
        new = run_osm_hunt(country)
        total_new += new
        if new > 0:
            log(f"   +{new} nouveaux leads insérés en base", Fore.GREEN)

        # Pause pour ne pas saturer Overpass
        time.sleep(5)

        # 2. Enrichir
        emails = run_email_crawl(country)
        total_emails += emails

        time.sleep(3)

        # 3. Analyser
        ai = run_ai_analysis(country)
        total_ai += ai

    elapsed = (datetime.now() - start).seconds
    log("\n" + "=" * 50, Fore.GREEN)
    log(f"✅ SESSION TERMINÉE en {elapsed}s", Fore.GREEN)
    log(f"   Leads collectés  : {total_new}", Fore.GREEN)
    log(f"   Emails trouvés   : {total_emails}", Fore.GREEN)
    log(f"   Leads analysés   : {total_ai}", Fore.GREEN)
    log("=" * 50, Fore.GREEN)


# ============================================================
# INIT SCHEDULER
# ============================================================

_scheduler = None  # Instance unique

def init_scheduler():
    """
    Initialise et démarre le scheduler APScheduler.
    À appeler une fois au lancement du serveur (après init_db).
    Retourne l'instance du scheduler.
    """
    global _scheduler

    _scheduler = BackgroundScheduler(timezone='Africa/Douala')

    # Job quotidien à l'heure configurée
    _scheduler.add_job(
        daily_prospection_job,
        trigger=CronTrigger(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE),
        id='daily_prospection',
        name='Prospection quotidienne BIOUND PRO',
        replace_existing=True,
        misfire_grace_time=3600  # Peut se déclencher jusqu'à 1h en retard si le serveur était éteint
    )

    _scheduler.start()

    next_run = _scheduler.get_job('daily_prospection').next_run_time
    log(f"✅ Scheduler actif — prochain lancement : {next_run.strftime('%d/%m/%Y à %H:%M')}", Fore.GREEN)

    return _scheduler


def shutdown_scheduler():
    """Arrête proprement le scheduler (appelé à l'exit du serveur)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log("Scheduler arrêté.", Fore.YELLOW)


def get_scheduler_status() -> dict:
    """Retourne l'état du scheduler pour l'API /api/scheduler."""
    global _scheduler

    if not _scheduler or not _scheduler.running:
        return {'running': False, 'jobs': []}

    jobs = []
    for job in _scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            'id':       job.id,
            'name':     job.name,
            'next_run': next_run.strftime('%d/%m/%Y %H:%M') if next_run else '—',
        })

    return {
        'running':    True,
        'hour':       SCHEDULE_HOUR,
        'minute':     SCHEDULE_MINUTE,
        'countries':  AUTO_COUNTRIES,
        'jobs':       jobs
    }


def trigger_now():
    """Force le lancement immédiat de la session (pour tests via API)."""
    t = threading.Thread(target=daily_prospection_job, daemon=True)
    t.start()
    log("🔥 Lancement manuel forcé en arrière-plan", Fore.CYAN)
