"""Database initialization and helpers."""
import sqlite3, os, sys

sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'institutions.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT,
        url TEXT UNIQUE,
        source TEXT,
        publish_date TEXT,
        tags TEXT,
        summary TEXT,
        summary_cn TEXT,
        fetch_status TEXT DEFAULT 'listed',
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    try:
        c.execute('ALTER TABLE articles ADD COLUMN summary_cn TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        c.execute('ALTER TABLE articles ADD COLUMN title_cn TEXT')
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn

def is_new_url(conn, url: str) -> bool:
    c = conn.cursor()
    c.execute('SELECT id FROM articles WHERE url=?', (url,))
    return c.fetchone() is None


def load_known_urls(conn, source: str) -> set:
    """Return set of URLs already in DB for a given source.
    Used for early termination in pagination loops.
    """
    c = conn.cursor()
    c.execute('SELECT url FROM articles WHERE source = ?', (source,))
    return set(r[0] for r in c.fetchall())


def load_known_urls_all(conn) -> set:
    """Return set of ALL URLs in DB (for multi-source crawlers)."""
    c = conn.cursor()
    c.execute('SELECT url FROM articles')
    return set(r[0] for r in c.fetchall())

def insert_article(conn, title, content, url, source, publish_date, summary='', title_cn=''):
    c = conn.cursor()
    c.execute('''INSERT INTO articles (title, content, url, source, publish_date, summary, title_cn)
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
             (title, content, url, source, publish_date, summary, title_cn))
    conn.commit()


def init_crawl_log():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Latest state per source
    c.execute('''CREATE TABLE IF NOT EXISTS crawl_log (
        source TEXT PRIMARY KEY,
        last_crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        articles_found INTEGER DEFAULT 0,
        articles_new INTEGER DEFAULT 0
    )''')
    # Full run history
    c.execute('''CREATE TABLE IF NOT EXISTS crawl_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        duration_sec REAL,
        articles_found INTEGER DEFAULT 0,
        articles_new INTEGER DEFAULT 0,
        status TEXT DEFAULT 'ok',
        error TEXT
    )''')
    conn.commit()
    return conn


def get_last_crawl(conn, source: str):
    c = conn.cursor()
    c.execute('SELECT last_crawled_at FROM crawl_log WHERE source = ?', (source,))
    row = c.fetchone()
    return row['last_crawled_at'] if row else None


def log_run_start(conn, source: str) -> int:
    """Insert a run record and return its ID."""
    c = conn.cursor()
    c.execute("INSERT INTO crawl_runs (source, started_at) VALUES (?, datetime('now'))", (source,))
    conn.commit()
    return c.lastrowid


def log_run_end(conn, run_id: int, articles_found: int, articles_new: int,
                duration_sec: float, error: str = None):
    """Update a run record with results."""
    status = 'error' if error else 'ok'
    c = conn.cursor()
    c.execute('''UPDATE crawl_runs SET duration_sec=?, articles_found=?, articles_new=?,
                 status=?, error=? WHERE id=?''',
              (duration_sec, articles_found, articles_new, status, error, run_id))
    conn.commit()


def update_crawl_log(conn, source: str, articles_found: int, articles_new: int):
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO crawl_log (source, last_crawled_at, articles_found, articles_new)
                 VALUES (?, datetime("now"), ?, ?)''', (source, articles_found, articles_new))
    conn.commit()


def view_log(limit: int = 20, source: str = None):
    """Print recent crawl run history."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if source:
        c.execute('''SELECT * FROM crawl_runs WHERE source=? ORDER BY id DESC LIMIT ?''',
                  (source, limit))
    else:
        c.execute('''SELECT * FROM crawl_runs ORDER BY id DESC LIMIT ?''', (limit,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        print('No crawl history yet.')
        return

    print(f'{"ID":>5} {"Source":<28s} {"Started":<19s} {"Dur":>6s} {"Found":>5s} {"New":>5s} {"Status"}')
    print('-' * 85)
    for r in reversed(rows):
        dur = f'{r["duration_sec"]:.0f}s' if r['duration_sec'] else '?'
        status = r['status']
        if r['error']:
            status += f' ({r["error"][:30]})'
        print(f'{r["id"]:>5} {r["source"]:<28s} {r["started_at"]:<19s} {dur:>6s} {r["articles_found"]:>5} {r["articles_new"]:>5} {status}')
