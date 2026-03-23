import sqlite3

DB_PATH = 'leads.db'

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT UNIQUE NOT NULL,
            source TEXT,
            name TEXT,
            website TEXT,
            city TEXT,
            country TEXT DEFAULT 'CM',
            category TEXT DEFAULT 'autre',
            product TEXT DEFAULT '',
            context TEXT DEFAULT '',
            ai_score INTEGER DEFAULT 0,
            digital_score INTEGER DEFAULT 0,
            ai_accroche TEXT DEFAULT '',
            service_needed TEXT DEFAULT '',
            besoin TEXT DEFAULT '',
            email TEXT DEFAULT '',
            status TEXT DEFAULT 'Nouveau',
            analyzed BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_number TEXT NOT NULL,
            type TEXT NOT NULL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(lead_number) REFERENCES leads(number)
        )
    ''')
    # Migration douce : ajoute les colonnes manquantes sans casser une DB existante
    _migrate(cursor)
    conn.commit()
    conn.close()

# ============================================================
# INTERACTIONS CRUD
# ============================================================

def add_interaction(number, interaction_type, note=""):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO interactions (lead_number, type, note)
        VALUES (?, ?, ?)
    ''', (number, interaction_type, note))
    conn.commit()
    conn.close()

def get_interactions(number):
    conn = get_db_connection()
    cursor = conn.cursor()
    rows = cursor.execute('''
        SELECT type, note, created_at FROM interactions 
        WHERE lead_number = ? 
        ORDER BY created_at DESC
    ''', (number,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
    print("✅ Base de données initialisée : leads.db")

def _migrate(cursor):
    """Ajoute les nouvelles colonnes si la DB existait déjà avant cette version."""
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(leads)")}
    migrations = {
        'country':       "ALTER TABLE leads ADD COLUMN country TEXT DEFAULT 'CM'",
        'ai_accroche':   "ALTER TABLE leads ADD COLUMN ai_accroche TEXT DEFAULT ''",
        'email':         "ALTER TABLE leads ADD COLUMN email TEXT DEFAULT ''",
        'besoin':        "ALTER TABLE leads ADD COLUMN besoin TEXT DEFAULT ''",
        'digital_score': "ALTER TABLE leads ADD COLUMN digital_score INTEGER DEFAULT 0",
    }
    for col, sql in migrations.items():
        if col not in existing:
            cursor.execute(sql)
            print(f"  → Migration : colonne '{col}' ajoutée")

def get_all_leads(country=None, category=None):
    """Récupère les leads avec filtres pays et catégorie optionnels."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM leads WHERE 1=1"
    params = []
    
    if country:
        countries = [c.strip() for c in country.split(',') if c.strip()]
        if countries:
            placeholders = ','.join('?' * len(countries))
            query += f" AND country IN ({placeholders})"
            params.extend(countries)
    
    if category and category != 'all':
        query += " AND category = ?"
        params.append(category)
        
    query += " ORDER BY created_at DESC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def add_lead(lead_data):
    """
    Insère un lead si le numéro n'existe pas déjà.
    Retourne {'status': 'added', 'lead': <dict>} ou {'status': 'exists'}.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    number = lead_data.get('number', '').strip()
    if not number:
        conn.close()
        return {'status': 'error', 'message': 'Le numéro est obligatoire'}

    try:
        cursor.execute('''
            INSERT INTO leads (
                number, source, name, website, city, country, category,
                product, context, ai_score, ai_accroche, service_needed,
                besoin, email, status, analyzed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            number,
            lead_data.get('source', 'manual'),
            lead_data.get('name', ''),
            lead_data.get('website', ''),
            lead_data.get('city', ''),
            lead_data.get('country', 'CM'),
            lead_data.get('category', 'autre'),
            lead_data.get('product', ''),
            lead_data.get('context', '')[:1000],
            lead_data.get('ai_score', 0),
            lead_data.get('ai_accroche', ''),
            lead_data.get('service_needed', ''),
            lead_data.get('besoin', ''),
            lead_data.get('email', ''),
            lead_data.get('status', 'Nouveau'),
            1 if lead_data.get('analyzed') else 0
        ))
        conn.commit()
        lead_id = cursor.lastrowid
        cursor.execute("SELECT * FROM leads WHERE id = ?", (lead_id,))
        new_lead = dict(cursor.fetchone())
        conn.close()
        return {'status': 'added', 'lead': new_lead}

    except sqlite3.IntegrityError:
        # Numéro déjà présent → déduplication
        conn.close()
        return {'status': 'exists'}
    except Exception as e:
        conn.close()
        return {'status': 'error', 'message': str(e)}

def update_lead(number, update_data):
    """
    Met à jour les champs autorisés d'un lead.
    Retourne le lead mis à jour ou None si introuvable.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    allowed = [
        'name', 'website', 'city', 'country', 'category', 'product',
        'context', 'ai_score', 'ai_accroche', 'service_needed',
        'besoin', 'email', 'status', 'analyzed'
    ]
    fields, values = [], []
    for key, value in update_data.items():
        if key in allowed:
            fields.append(f"{key} = ?")
            values.append(value)

    if not fields:
        conn.close()
        return None

    values.append(number)
    cursor.execute(
        f"UPDATE leads SET {', '.join(fields)} WHERE number = ?",
        values
    )
    conn.commit()

    if cursor.rowcount > 0:
        cursor.execute("SELECT * FROM leads WHERE number = ?", (number,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    conn.close()
    return None

def delete_lead(number):
    """Supprime un lead par numéro. Retourne True si supprimé."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM leads WHERE number = ?", (number,))
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0

def get_pipeline_stats(country='CM'):
    """Alias léger pour le scheduler."""
    return get_dashboard_stats(country)

def get_dashboard_stats(country=''):
    """
    Retourne toutes les statistiques pour le dashboard :
    Pipeline, totaux, taux, historique 7j, catégories, top leads.
    """
    from datetime import date, timedelta
    conn = get_db_connection()
    c = conn.cursor()

    countries = [c_strip for c_strip in country.split(',') if c_strip.strip()]
    
    where_clause = ""
    params = ()
    if countries:
        placeholders = ','.join('?' * len(countries))
        where_clause = f"WHERE country IN ({placeholders})"
        params = tuple(countries)

    # Pipeline par statut
    sql_pipe = f"SELECT status, COUNT(*) as n FROM leads {where_clause} GROUP BY status"
    c.execute(sql_pipe, params)
    pipeline = {row['status']: row['n'] for row in c.fetchall()}

    # Totaux globaux (filtrés par pays)
    c.execute(f"SELECT COUNT(*) as n FROM leads {where_clause}", params)
    total = c.fetchone()['n'] or 0

    c.execute(f"SELECT COUNT(*) as n FROM leads {where_clause} {'AND' if countries else 'WHERE'} analyzed=1", params)
    analyzed = c.fetchone()['n'] or 0

    c.execute(f"SELECT COUNT(*) as n FROM leads {where_clause} {'AND' if countries else 'WHERE'} email != ''", params)
    with_email = c.fetchone()['n'] or 0

    converti = pipeline.get('Converti', 0)

    # Historique leads/jour sur 7 jours
    c.execute(f"""
        SELECT DATE(created_at) as day, COUNT(*) as n
        FROM leads WHERE country IN ({placeholders}) AND created_at >= DATE('now', '-6 days')
        GROUP BY day ORDER BY day ASC
    """, params)
    history_raw = {row['day']: row['n'] for row in c.fetchall()}
    history = []
    for i in range(7):
        d = (date.today() - timedelta(days=6 - i)).isoformat()
        history.append({'date': d, 'count': history_raw.get(d, 0)})

    # Répartition par catégorie
    c.execute(f"""
        SELECT category, COUNT(*) as n FROM leads
        WHERE country IN ({placeholders}) GROUP BY category ORDER BY n DESC LIMIT 8
    """, params)
    by_category = [{'cat': row['category'] or 'autre', 'count': row['n']} for row in c.fetchall()]

    # Top 5 leads par score IA
    c.execute(f"""
        SELECT number, name, category, ai_score, status, email
        FROM leads WHERE country IN ({placeholders}) AND ai_score > 0
        ORDER BY ai_score DESC LIMIT 5
    """, params)
    top_leads = [dict(row) for row in c.fetchall()]

    conn.close()

    return {
        'pipeline': {
            'Nouveau':  pipeline.get('Nouveau', 0),
            'Contacté': pipeline.get('Contacté', 0),
            'Répondu':  pipeline.get('Répondu', 0),
            'Converti': converti,
        },
        'total':           total,
        'analyzed':        analyzed,
        'with_email':      with_email,
        'converti':        converti,
        'rate_analysis':   round(analyzed   / total * 100),
        'rate_email':      round(with_email / total * 100),
        'rate_conversion': round(converti   / max(total, 1) * 100),
        'history':         history,
        'by_category':     by_category,
        'top_leads':       top_leads,
    }