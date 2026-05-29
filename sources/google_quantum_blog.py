"""Google Quantum AI Blog crawler - standalone, sitemap-based from blog.google."""
import sys, os, re, json, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SITEMAP_URL = 'https://blog.google/en-us/sitemap.xml'
SOURCE_NAME = 'Google Quantum AI'
KEYWORD_RE = re.compile(r'(?:quantum|willow|sycamore|qubit|qsim)', re.I)
DATE_RE = re.compile(
    r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})'
)


def crawl_sitemap():
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    urls = set()
    for m in re.finditer(r'<loc>(https://blog\.google/[^<]+)</loc>', resp.text):
        url = m.group(1)
        if KEYWORD_RE.search(url):
            urls.add(url)
    return sorted(urls)


def fetch_detail(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'date': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        title = ''
        d = ''

        # Title and date from JSON-LD
        for script in soup.find_all('script', type='application/ld+json'):
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get('@type') in ('NewsArticle', 'Article', 'BlogPosting'):
                title = data.get('headline', '')
                dp = data.get('datePublished', '')
                if dp:
                    try:
                        d = dp[:10]
                    except (ValueError, IndexError):
                        pass
                break

        # Fallback title
        if not title:
            og = soup.find('meta', property='og:title')
            if og:
                title = og.get('content', '').strip()

        # Fallback date from text
        if not d:
            text = soup.get_text()
            m = DATE_RE.search(text)
            if m:
                try:
                    d = datetime.strptime(m.group(1), '%d %b %Y').strftime('%Y-%m-%d')
                except ValueError:
                    pass

        # Content
        article = soup.find('article')
        content = article.get_text(separator='\n', strip=True) if article else soup.get_text(separator='\n', strip=True)

        return {'content': content, 'date': d, 'title': title}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


if __name__ == '__main__':
    print(f'[CRAWL] {SOURCE_NAME}: {SITEMAP_URL}')

    urls = crawl_sitemap()
    print(f'  Found {len(urls)} quantum URLs in sitemap')

    conn = init_db()
    client = get_llm()
    new_count = 0

    for url in urls:
        if not is_new_url(conn, url):
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
                (title, content, url, SOURCE_NAME, pub_date, summary, title_cn),
            )
            conn.commit()
            new_count += 1
            print(f'  [{pub_date}] {title[:80]}')
        except sqlite3.IntegrityError:
            pass

    conn.close()
    print(f'[OK] {new_count} new articles from {SOURCE_NAME}')
