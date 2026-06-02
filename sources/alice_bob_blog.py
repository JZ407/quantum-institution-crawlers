"""Alice & Bob Blog crawler — WordPress REST API based.

Alice & Bob: cat-qubit quantum computing company (Paris/Boston).
Blog covers technical deep dives, quantum error correction, engineering.

Usage: python sources/alice_bob_blog.py
"""
import sys, os, re, json, time, requests
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import DB_PATH, init_db, is_new_url
from core.llm import get_llm

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko)'
    )
}
API_BASE = 'https://alice-bob.com/wp-json/wp/v2'
SOURCE_NAME = 'Alice & Bob Blog'
PER_PAGE = 50   # WP REST API max per_page
# Custom post type: 'blog' (not 'posts')


def discover_posts():
    """Discover all blog posts via WP REST API with pagination."""
    all_posts = []
    page = 1

    while True:
        url = f'{API_BASE}/blog?per_page={PER_PAGE}&page={page}&_fields=id,date,title,link,slug'
        print(f'  API page {page}...')
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except Exception as e:
            print(f'  [WARN] API request failed page {page}: {e}')
            break

        if resp.status_code != 200:
            print(f'  [WARN] API returned {resp.status_code} on page {page}')
            break

        try:
            posts = resp.json()
        except json.JSONDecodeError as e:
            print(f'  [WARN] JSON decode error page {page}: {e}')
            break

        if not posts or not isinstance(posts, list):
            break

        for p in posts:
            title = p.get('title', {})
            if isinstance(title, dict):
                title = title.get('rendered', '')
            else:
                title = str(title)

            date = p.get('date', '')[:10]  # YYYY-MM-DD

            all_posts.append({
                'url': p.get('link', ''),
                'date': date,
                'title': title.strip(),
            })

        total_pages = int(resp.headers.get('X-WP-TotalPages', '0'))
        if page >= total_pages or len(posts) < PER_PAGE:
            break
        page += 1
        time.sleep(0.5)

    return all_posts


def fetch_detail(url):
    """Extract title, date, and clean content from a blog detail page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'title': '', 'date': '', 'content': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        # --- Title ---
        # Priority: og:title > h1
        title = ''
        og = soup.find('meta', property='og:title')
        if og and og.get('content'):
            title = og['content'].strip()
        if not title:
            h1 = soup.find('h1')
            if h1:
                title = h1.get_text(strip=True)

        # --- Date ---
        # Priority: meta published_time > JSON-LD > text regex
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
            # Fallback: text regex "Month DD, YYYY"
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
        # Priority: <article> > .rich-text > body
        content = ''
        article = soup.find('article')
        if article:
            content = article.get_text(separator='\n', strip=True)
        else:
            # WordPress blog content area
            entry = soup.find(class_=re.compile(r'entry-content|post-content|blog-content'))
            if entry:
                content = entry.get_text(separator='\n', strip=True)

        if not content:
            # Last resort: body text (trimmed)
            body = soup.find('body')
            if body:
                content = body.get_text(separator='\n', strip=True)

        # Trim: cut before any stop keyword at the tail 40%
        if content:
            stop_kws = [
                'Related Posts', 'You may also like', 'Share this',
                'About the Author', 'Leave a comment', 'Subscribe to',
                'Tags:', 'Categories:', 'Previous Post', 'Next Post',
                'Copyright', 'Privacy Policy', 'Cookie Policy',
            ]
            best_cut = len(content)
            tail_start = len(content) * 3 // 5  # search last 40%
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
    print(f'[CRAWL] {SOURCE_NAME}: WP REST API')
    posts = discover_posts()
    print(f'  Discovered {len(posts)} blog posts')

    conn = init_db()
    client = get_llm()
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

        # Summary
        summary = content[:300].strip() if content else ''

        # Chinese title translation
        title_cn = ''
        if title and client and not any('一' <= c <= '鿿' for c in title[:20]):
            try:
                tn_msg = [
                    {'role': 'system', 'content': '将以下英文新闻标题翻译为中文。只输出中文，不要解释。'},
                    {'role': 'user', 'content': title},
                ]
                title_cn = client.chat(tn_msg).strip()[:200]
            except Exception:
                pass

        # Insert
        conn.execute(
            'INSERT OR IGNORE INTO articles (title, content, url, source, publish_date, summary, title_cn) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (title, content, p['url'], SOURCE_NAME, date, summary, title_cn),
        )
        new_count += 1
        time.sleep(1)  # polite delay

    conn.commit()
    print(f'[OK] {SOURCE_NAME}: {new_count} new, {skip_count} skipped')
    conn.close()
