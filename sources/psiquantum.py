"""PsiQuantum news crawler - standalone, sitemap-based."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SITEMAP_URL = 'https://www.psiquantum.com/sitemap.xml'
SOURCE_NAME = 'PsiQuantum'
DATE_RE = re.compile(
    r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})'
)


def crawl_sitemap():
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=15)
    articles = []

    for block in re.findall(r'<url>(.*?)</url>', resp.text, re.DOTALL):
        loc = re.search(r'<loc>(.*?)</loc>', block)
        lm = re.search(r'<lastmod>(.*?)</lastmod>', block)
        if not loc or '/news-import/' not in loc.group(1):
            continue
        url = loc.group(1)
        # Skip category/tag/empty pages
        if '/category/' in url or '/tag/' in url:
            continue
        lastmod = lm.group(1)[:10] if lm else ''
        articles.append({'url': url, 'date': lastmod})

    return articles


def fetch_detail(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'date': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Title from h1
        title = ''
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)

        # Date from text
        d = ''
        text = soup.get_text()
        m = DATE_RE.search(text)
        if m:
            try:
                d = datetime.strptime(m.group(1), '%B %d, %Y').strftime('%Y-%m-%d')
            except ValueError:
                pass

        # Content
        content_div = soup.find(class_=re.compile(r'content'))
        if content_div:
            content = content_div.get_text(separator='\n', strip=True)
        else:
            article = soup.find('article')
            content = article.get_text(separator='\n', strip=True) if article else text

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

        time.sleep(0.2)

    conn.close()
    print(f'[OK] {new_count} new articles from {SOURCE_NAME}')
