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
    conn.commit()
    return conn

def is_new_url(conn, url: str) -> bool:
    c = conn.cursor()
    c.execute('SELECT id FROM articles WHERE url=?', (url,))
    return c.fetchone() is None

def insert_article(conn, title, content, url, source, publish_date, summary='', summary_cn=''):
    c = conn.cursor()
    c.execute('''INSERT INTO articles (title, content, url, source, publish_date, summary, summary_cn)
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
             (title, content, url, source, publish_date, summary, summary_cn))
    conn.commit()


def init_crawl_log():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS crawl_log (
        source TEXT PRIMARY KEY,
        last_crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        articles_found INTEGER DEFAULT 0,
        articles_new INTEGER DEFAULT 0
    )''')
    conn.commit()
    return conn


def get_last_crawl(conn, source: str):
    c = conn.cursor()
    c.execute('SELECT last_crawled_at FROM crawl_log WHERE source = ?', (source,))
    row = c.fetchone()
    return row['last_crawled_at'] if row else None


def update_crawl_log(conn, source: str, articles_found: int, articles_new: int):
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO crawl_log (source, last_crawled_at, articles_found, articles_new)
                 VALUES (?, datetime(\"now\"), ?, ?)''', (source, articles_found, articles_new))
    conn.commit()
