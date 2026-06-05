"""Classiq insights crawler - standalone, sitemap-based."""
import sys, os, re, json, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url, load_known_urls

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SITEMAP_URL = 'https://www.classiq.io/sitemap.xml'
SOURCE_NAME = 'Classiq'


def crawl_sitemap():
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=15)
    articles = []
    for m in re.finditer(r'<loc>(https://www\.classiq\.io/insights/[^<]+)</loc>', resp.text):
        articles.append({'url': m.group(1), 'date': ''})
    return articles


def fetch_detail(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'date': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        title = ''
        d = ''

        # JSON-LD
        for script in soup.find_all('script', type='application/ld+json'):
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and data.get('@type') == 'Article':
                    title = data.get('headline', '')
                    dp = data.get('datePublished', '')
                    if dp:
                        try:
                            d = datetime.strptime(dp, '%b %d, %Y').strftime('%Y-%m-%d')
                        except ValueError:
                            pass
                    break
            except json.JSONDecodeError:
                pass

        # Fallback title
        if not title:
            h1 = soup.find('h1')
            if h1:
                title = h1.get_text(strip=True)

        # Content
        rt = soup.find(class_=re.compile(r'rich-text'))
        if rt:
            content = rt.get_text(separator='\n', strip=True)
        else:
            content = soup.get_text(separator='\n', strip=True)

        return {'content': content, 'date': d, 'title': title}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


if __name__ == '__main__':
    print(f'[CRAWL] {SOURCE_NAME}: {SITEMAP_URL}')

    articles = crawl_sitemap()
    print(f'  Found {len(articles)} articles')

    conn = init_db()
    # Load known URLs for fast dedup (in-memory instead of per-URL SQL query)
    known_urls = load_known_urls(conn, 'Classiq')
    client = get_llm()
    new_count = 0

    for art in articles:
        if art['url'] in known_urls:
            continue

        detail = fetch_detail(art['url'])
        content = detail['content']
        pub_date = detail['date'] or art['date']
        title = detail['title']
        if not title:
            title = art['url'].rstrip('/').split('/')[-1].replace('-', ' ').title()

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
                (title, content, art['url'], SOURCE_NAME, pub_date, summary, title_cn),
            )
            conn.commit()
            new_count += 1
            print(f'  [{pub_date}] {title[:80]}')
        except sqlite3.IntegrityError:
            pass

        time.sleep(0.15)

    conn.close()
    print(f'[OK] {new_count} new articles from {SOURCE_NAME}')
