"""Google Research quantum blog + papers crawler - standalone, sitemap from research.google."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url, load_known_urls

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SITEMAP_URL = 'https://research.google/sitemap_main.xml'
KEYWORD_RE = re.compile(r'(?:quantum|willow|sycamore|qubit|qsim)', re.I)
DATE_RE = re.compile(
    r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})'
)


def crawl_sitemap():
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    blogs = set()
    pubs = set()

    for m in re.finditer(r'<loc>(https://research\.google/[^<]+)</loc>', resp.text):
        url = m.group(1)
        if not KEYWORD_RE.search(url):
            continue
        if '/blog/' in url:
            blogs.add(url)
        elif '/pubs/' in url:
            pubs.add(url)

    return sorted(blogs), sorted(pubs)


def fetch_detail(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'date': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Title from og:title
        title = ''
        og = soup.find('meta', property='og:title')
        if og:
            title = og.get('content', '').strip()

        # Date: first text date (most likely the article date)
        d = ''
        main_el = soup.find('main')
        search_text = main_el.get_text() if main_el else soup.get_text()
        m = DATE_RE.search(search_text)
        if m:
            try:
                d = datetime.strptime(m.group(1), '%B %d, %Y').strftime('%Y-%m-%d')
            except ValueError:
                pass

        # Content from main
        content = search_text if main_el else soup.get_text(separator='\n', strip=True)

        return {'content': content, 'date': d, 'title': title}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


def process_urls(urls, source_name, conn, client):
    new_count = 0
    for url in urls:
        if url in known_urls:
            continue

        detail = fetch_detail(url)
        content = detail['content']
        pub_date = detail['date']
        title = detail['title']
        if not title:
            title = url.rstrip('/').split('/')[-1].replace('-', ' ').title()

        summary = content[:300].strip() if content else ''

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

        try:
            c = conn.cursor()
            c.execute(
                '''INSERT INTO articles (title, content, url, source, publish_date, summary, title_cn)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (title, content, url, source_name, pub_date, summary, title_cn),
            )
            conn.commit()
            new_count += 1
            print(f'  [{pub_date}] {title[:80]}')
        except sqlite3.IntegrityError:
            pass

        time.sleep(0.2)

    return new_count


if __name__ == '__main__':
    print(f'[CRAWL] Google Research: {SITEMAP_URL}')

    blog_urls, pub_urls = crawl_sitemap()
    print(f'  Blog posts: {len(blog_urls)}')
    print(f'  Publications: {len(pub_urls)}')
    print(f'  Total: {len(blog_urls) + len(pub_urls)}')

    conn = init_db()
    # Load known URLs for fast in-memory dedup
    known_urls = load_known_urls(conn, 'Google Research')
    client = get_llm()

    # Blog posts
    print(f'\n--- Google Research Blog ({len(blog_urls)} URLs) ---')
    blog_new = process_urls(blog_urls, 'Google Research Blog', conn, client)

    # Publications
    print(f'\n--- Google Research Papers ({len(pub_urls)} URLs) ---')
    pub_new = process_urls(pub_urls, 'Google Research Papers', conn, client)

    conn.close()
    print(f'\n[OK] {blog_new} new blog posts + {pub_new} new papers = {blog_new + pub_new} total')
