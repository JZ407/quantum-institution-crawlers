"""Q-CTRL blog crawler - standalone, sitemap-based."""
import sys, os, re, json, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SITEMAP_URL = 'https://q-ctrl.com/sitemap.xml'
SOURCE_NAME = 'Q-CTRL'


def crawl_sitemap():
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=15)
    articles = []
    for m in re.finditer(r'<loc>(https://q-ctrl\.com/blog/[^<]+)</loc>', resp.text):
        url = m.group(1)
        # Skip category/tag/index pages
        path = url.replace('https://q-ctrl.com/blog/', '')
        if not path or path.endswith('/') or '?' in path:
            continue
        articles.append({'url': url, 'date': ''})
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
            og = soup.find('meta', property='og:title')
            if og:
                title = re.sub(r'\s*\|\s*Q-CTRL\s*$', '', og.get('content', '')).strip()

        # Content
        article = soup.find('article')
        if article:
            content = article.get_text(separator='\n', strip=True)
        else:
            rt = soup.find(class_=re.compile(r'rich-text'))
            content = rt.get_text(separator='\n', strip=True) if rt else soup.get_text(separator='\n', strip=True)

        return {'content': content, 'date': d, 'title': title}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


if __name__ == '__main__':
    print(f'[CRAWL] {SOURCE_NAME}: {SITEMAP_URL}')

    articles = crawl_sitemap()
    print(f'  Found {len(articles)} articles')

    conn = init_db()
    client = get_llm()
    new_count = 0

    for art in articles:
        if not is_new_url(conn, art['url']):
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
