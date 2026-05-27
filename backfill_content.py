"""Re-fetch content for articles that were truncated at old limits (3000/5000 chars)."""
import sys, os, io, sqlite3, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.base import BaseCrawler

conn = sqlite3.connect('D:/Claude_code/institution_news/institutions.db')
c = conn.cursor()

# Find truncated articles: content ends at exactly old limits
c.execute("SELECT id, title, url, source, LENGTH(content) FROM articles WHERE LENGTH(content) IN (2999, 3000, 3001, 4999, 5000, 5001)")
rows = c.fetchall()
conn.close()

print(f'Found {len(rows)} truncated articles')

if not rows:
    print('Nothing to do.')
    sys.exit(0)

# Use BaseCrawler's _fetch_detail for each
dummy_source = {'name': 'backfill', 'type': 'enterprise', 'url': '', 'url_pattern': '/'}
crawler = BaseCrawler(dummy_source)

updated = 0
for art_id, title, url, source, old_len in rows:
    short = title[:60] if title else url[:60]
    print(f'[{art_id}] {short} ({old_len} chars) ... ', end='', flush=True)
    try:
        detail = crawler._fetch_detail(url)
        new_content = detail['content']
        new_len = len(new_content)
        if new_len > old_len + 100:  # significant improvement
            conn2 = sqlite3.connect('D:/Claude_code/institution_news/institutions.db')
            conn2.execute('UPDATE articles SET content = ? WHERE id = ?', (new_content, art_id))
            conn2.commit()
            conn2.close()
            updated += 1
            print(f'-> {new_len} chars')
        else:
            print(f'no improvement ({new_len} chars)')
    except Exception as e:
        print(f'ERROR: {e}')
    time.sleep(0.3)

print(f'\nDone: {updated}/{len(rows)} updated')
