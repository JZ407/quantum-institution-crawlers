"""IBM Quantum Blog crawler - standalone, ?page={n} pagination."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
BASE = 'https://www.ibm.com/quantum/blog'
SOURCE_NAME = 'IBM Quantum Blog'
DATE_RE = re.compile(
    r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})'
)
MONTHS = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}


def _parse_date(text):
    m = DATE_RE.search(text)
    if not m:
        return ''
    try:
        return datetime.strptime(m.group(1), '%d %b %Y').strftime('%Y-%m-%d')
    except ValueError:
        return ''


def crawl_listing():
    s = requests.Session()
    articles = []
    seen_urls = set()

    prev_articles = None
    for page in range(1, 30):
        if page == 1:
            url = BASE
        else:
            url = f'{BASE}?page={page}'

        resp = s.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')

        page_articles = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/quantum/blog/' not in href or '?' in href:
                continue
            text = a.get_text(strip=True)
            if len(text) < 15:
                continue

            full_url = 'https://www.ibm.com' + href if href.startswith('/') else href
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            d = _parse_date(text)
            if not d:
                continue

            # Title is everything before the date in the link text
            m = DATE_RE.search(text)
            title = text[:m.start()].strip() if m else text
            page_articles.append({'title': title, 'url': full_url, 'date': d})

        print(f'  Page {page}: {len(page_articles)} found')

        if not page_articles:
            break

        # Stop if this page is identical to previous (end of content)
        page_urls = tuple(a['url'] for a in page_articles)
        if prev_articles and page_urls == prev_articles:
            break
        prev_articles = page_urls

        articles.extend(page_articles)
        time.sleep(0.3)

    return articles


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
            title = og.get('content', '')
            title = re.sub(r'\s*\|\s*IBM Quantum Computing Blog\s*$', '', title).strip()

        # Date from text "Date DD Mon YYYY"
        text = soup.get_text()
        d = ''
        m = re.search(
            r'Date\s*\n?\s*(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})',
            text,
        )
        if m:
            try:
                d = datetime.strptime(m.group(1), '%d %b %Y').strftime('%Y-%m-%d')
            except ValueError:
                pass

        # Content from post-body div
        body_div = soup.find('div', class_=re.compile(r'post-body'))
        if body_div:
            content = body_div.get_text(separator='\n', strip=True)
        else:
            content = soup.get_text(separator='\n', strip=True)

        return {'content': content, 'date': d, 'title': title}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


if __name__ == '__main__':
    print(f'[CRAWL] {SOURCE_NAME}: {BASE}')

    articles = crawl_listing()
    print(f'  Total: {len(articles)} articles')

    conn = init_db()
    client = get_llm()
    new_count = 0

    for art in articles:
        if not is_new_url(conn, art['url']):
            continue

        detail = fetch_detail(art['url'])
        content = detail['content']
        pub_date = detail['date'] or art['date']
        best_title = detail['title'] or art['title']

        summary = content[:300].strip() if content else ''

        # Translate title
        title_cn = ''
        if best_title and client and not any('一' <= c <= '鿿' for c in best_title[:20]):
            try:
                tn_msg = [
                    {'role': 'system', 'content': '将以下英文新闻标题翻译为中文。只输出中文，不要解释。'},
                    {'role': 'user', 'content': best_title},
                ]
                title_cn = client.chat(tn_msg).strip()[:200]
            except Exception:
                pass

        try:
            c = conn.cursor()
            c.execute(
                '''INSERT INTO articles (title, content, url, source, publish_date, summary, title_cn)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (best_title, content, art['url'], SOURCE_NAME, pub_date, summary, title_cn),
            )
            conn.commit()
            new_count += 1
        except sqlite3.IntegrityError:
            pass

    conn.close()
    print(f'[OK] {new_count} new articles from {SOURCE_NAME}')
