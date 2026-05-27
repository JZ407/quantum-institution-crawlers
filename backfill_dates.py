import sys, os, re, json as _json, sqlite3, io, time
from datetime import datetime
import requests
from bs4 import BeautifulSoup

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crawl_institutions import fetch_detail, DB_PATH

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("SELECT id, title, url, publish_date FROM articles WHERE publish_date IS NULL OR publish_date = ''")
rows = c.fetchall()
conn.close()

print(f'Found {len(rows)} articles without dates\n')

updated = 0
for art_id, title, url, _ in rows:
    # Skip non-article URLs (landing pages)
    if not re.search(r'/(blog|news|article|press|technology)/', url, re.I):
        short = url.rsplit('/', 2)[-1] if '/' in url else url
        print(f'[{art_id}] SKIP (landing page): {short}')
        continue

    short = url.rsplit('/', 1)[-1] if '/' in url else url
    print(f'[{art_id}] {short} ... ', end='', flush=True)
    detail = fetch_detail(url)
    d = detail['date']
    if d:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('UPDATE articles SET publish_date = ? WHERE id = ?', (d, art_id))
        conn.commit()
        conn.close()
        updated += 1
        print(d)
    else:
        print('no date')
    time.sleep(0.3)

print(f'\nDone: {updated}/{len(rows)} updated')
