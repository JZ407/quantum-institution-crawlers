"""IBM Quantum Press Releases crawler - standalone, newsroom search pagination."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url, load_known_urls

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SEARCH_URL = 'https://newsroom.ibm.com/index.php'
SEARCH_PARAMS = {'s': '20322', 'l': '50', 'query': 'quantum computers'}
SOURCE_NAME = 'IBM Quantum PR'
DATE_RE = re.compile(
    r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})'
)
# Extract date from URL like YYYY-MM-DD
URL_DATE_RE = re.compile(r'/(\d{4})-(\d{2})-(\d{2})-')


def _parse_date(text):
    m = DATE_RE.search(text)
    if not m:
        return ''
    try:
        return datetime.strptime(m.group(1), '%d %b %Y').strftime('%Y-%m-%d')
    except ValueError:
        return ''


def _is_quantum_pr(url, text):
    """Check if URL or link text is quantum-related."""
    combined = (url + text).lower()
    return any(kw in combined for kw in ['quantum', 'qubit', 'qiskit'])


def crawl_listing():
    s = requests.Session()
    articles = {}
    seen_urls = set()

    for offset in range(0, 500, 50):
        params = {**SEARCH_PARAMS, 'o': str(offset)}
        resp = s.get(SEARCH_URL, params=params, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')

        page_articles = {}
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)

            if 'newsroom.ibm.com/' not in href:
                continue
            if 'index.php' in href or 'campaign?item=' in href or 'media-center?' in href:
                continue
            if len(text) < 15:
                continue
            if not _is_quantum_pr(href, text):
                continue

            # Clean fragment
            clean_url = re.sub(r'#.*', '', href)
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)

            # Only add if we don't have it yet or this text is longer (more informative)
            if clean_url not in page_articles or len(text) > len(page_articles[clean_url]):
                page_articles[clean_url] = text

        for url, text in page_articles.items():
            if url not in articles:
                articles[url] = text

        print(f'  Offset {offset}: {len(page_articles)} found, {len(articles)} unique so far')

        if not page_articles:
            break

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
            title = og.get('content', '').strip()

        # Date: try text first, then URL pattern
        text = soup.get_text()
        d = _parse_date(text)
        if not d:
            m = URL_DATE_RE.search(url)
            if m:
                d = f'{m.group(1)}-{m.group(2)}-{m.group(3)}'

        # Content
        content_div = soup.find('div', class_=re.compile(r'wd_content'))
        if content_div:
            content = content_div.get_text(separator='\n', strip=True)
        else:
            main = soup.find('main')
            content = main.get_text(separator='\n', strip=True) if main else text

        return {'content': content, 'date': d, 'title': title}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


if __name__ == '__main__':
    print(f'[CRAWL] {SOURCE_NAME}: newsroom.ibm.com quantum search')

    articles = crawl_listing()
    print(f'  Total: {len(articles)} unique PRs')

    conn = init_db()
    # Load known URLs for fast dedup (in-memory instead of per-URL SQL query)
    known_urls = load_known_urls(conn, 'IBM Quantum PR')
    client = get_llm()
    new_count = 0

    for url, listing_text in articles.items():
        if url in known_urls:
            continue

        detail = fetch_detail(url)
        content = detail['content']
        pub_date = detail['date']
        title = detail['title'] or listing_text[:200]

        summary = content[:300].strip() if content else ''

        # Translate title
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
        except sqlite3.IntegrityError:
            pass

    conn.close()
    print(f'[OK] {new_count} new articles from {SOURCE_NAME}')
