"""Alice & Bob Newsroom crawler — WordPress REST API 'news' custom post type.

Alice & Bob newsroom contains press releases, partnership announcements,
and media coverage. URL pattern: /newsroom/slug/

Usage: python sources/alice_bob_newsroom.py
"""
import sys, os, re, json, time, requests
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko)'
    )
}
API_BASE = 'https://alice-bob.com/wp-json/wp/v2'
SOURCE_NAME = 'Alice & Bob Newsroom'
PER_PAGE = 50


def discover_posts():
    """Discover newsroom posts via WP REST API custom post type 'news'."""
    all_posts = []
    page = 1

    while True:
        url = f'{API_BASE}/news?per_page={PER_PAGE}&page={page}&_fields=id,date,title,link,slug'
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except Exception as e:
            print(f'  [WARN] API failed page {page}: {e}')
            break
        if resp.status_code != 200:
            print(f'  [WARN] API returned {resp.status_code} on page {page}')
            break
        try:
            posts = resp.json()
        except json.JSONDecodeError:
            break
        if not posts:
            break

        for p in posts:
            title = p.get('title', {})
            if isinstance(title, dict):
                title = title.get('rendered', '')
            all_posts.append({
                'url': p.get('link', ''),
                'date': p.get('date', '')[:10],
                'title': str(title or '').strip(),
            })

        total_pages = int(resp.headers.get('X-WP-TotalPages', '0'))
        if page >= total_pages or len(posts) < PER_PAGE:
            break
        page += 1
        time.sleep(0.5)

    return all_posts


def fetch_detail(url):
    """Extract title, date, and clean content from a newsroom detail page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'title': '', 'date': '', 'content': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        # --- Title ---
        title = ''
        og = soup.find('meta', property='og:title')
        if og and og.get('content'):
            title = og['content'].strip()
        if not title:
            h1 = soup.find('h1')
            if h1:
                title = h1.get_text(strip=True)

        # --- Date ---
        date = ''
        for meta_prop in ['article:published_time', 'article:modified_time']:
            meta = soup.find('meta', property=meta_prop)
            if meta and meta.get('content', '').strip():
                d = meta['content'].strip()[:10]
                if re.match(r'\d{4}-\d{2}-\d{2}', d):
                    date = d
                    break
        if not date:
            for script in soup.find_all('script', type='application/ld+json'):
                if not script.string:
                    continue
                try:
                    data = json.loads(script.string)
                    if isinstance(data, dict) and 'datePublished' in data:
                        d = data['datePublished'][:10]
                        if re.match(r'\d{4}-\d{2}-\d{2}', d):
                            date = d
                            break
                except json.JSONDecodeError:
                    pass
        if not date:
            m = re.search(
                r'(January|February|March|April|May|June|'
                r'July|August|September|October|November|December)'
                r'\s+\d{1,2},\s+(\d{4})',
                soup.get_text()[:2000]
            )
            if m:
                try:
                    date = datetime.strptime(m.group(0), '%B %d, %Y').strftime('%Y-%m-%d')
                except ValueError:
                    pass

        # --- Content ---
        content = ''
        article = soup.find('article')
        if article:
            content = article.get_text(separator='\n', strip=True)
        else:
            entry = soup.find(class_=re.compile(r'entry-content|post-content'))
            if entry:
                content = entry.get_text(separator='\n', strip=True)
        if not content:
            body = soup.find('body')
            if body:
                content = body.get_text(separator='\n', strip=True)

        # Trim tail noise
        if content:
            stop_kws = [
                'Related Posts', 'You may also like', 'Share this',
                'About the Author', 'Subscribe to', 'Tags:', 'Categories:',
                'Previous Post', 'Next Post', 'Copyright', 'Privacy Policy',
                'Media Contact', 'Press Contact',
            ]
            best_cut = len(content)
            tail_start = len(content) * 3 // 5
            for kw in stop_kws:
                pos = content.find(kw, tail_start)
                if pos != -1 and pos < best_cut:
                    best_cut = pos
            content = content[:best_cut].strip()

        return {'title': title, 'date': date, 'content': content}

    except Exception as e:
        print(f'  [WARN] fetch_detail failed for {url}: {e}')
        return {'title': '', 'date': '', 'content': ''}


if __name__ == '__main__':
    print(f'[CRAWL] {SOURCE_NAME}: WP REST API /news')
    posts = discover_posts()
    print(f'  Discovered {len(posts)} newsroom posts')

    conn = init_db()
    new_count, skip_count = 0, 0

    for i, p in enumerate(posts):
        if not is_new_url(conn, p['url']):
            skip_count += 1
            continue

        print(f'  [{i+1}/{len(posts)}] {p["title"][:70]}...')
        detail = fetch_detail(p['url'])

        title = detail['title'] or p['title']
        date = detail['date'] or p['date']
        content = detail['content']

        if not content:
            print(f'    [WARN] Empty content, skipping')
            continue

        conn.execute(
            'INSERT OR IGNORE INTO articles (title, content, url, source, publish_date) VALUES (?, ?, ?, ?, ?)',
            (title, content, p['url'], SOURCE_NAME, date)
        )
        new_count += 1
        time.sleep(1)

    conn.commit()
    print(f'[OK] {SOURCE_NAME}: {new_count} new, {skip_count} skipped')
    conn.close()
