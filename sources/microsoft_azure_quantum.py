"""Microsoft Azure Quantum Blog crawler - standalone, page/{n} pagination."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
BASE = 'https://azure.microsoft.com/en-us/blog/quantum/'
SOURCE_NAME = 'Microsoft Azure Quantum'
# Date in URL: /quantum/YYYY/MM/DD/slug/
URL_DATE_RE = re.compile(r'/quantum/(\d{4})/(\d{2})/(\d{2})/')


def crawl_listing():
    s = requests.Session()
    articles = []
    seen_urls = set()

    for page in range(1, 20):
        if page == 1:
            url = BASE
        else:
            url = f'{BASE}page/{page}/'

        resp = s.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')

        page_articles = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if '/quantum/' not in href or len(text) < 20:
                continue
            # Skip product/category pages (no date in URL)
            if not URL_DATE_RE.search(href):
                continue

            full_url = 'https://azure.microsoft.com' + href if href.startswith('/') else href
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            # Extract date from URL
            m = URL_DATE_RE.search(href)
            d = f'{m.group(1)}-{m.group(2)}-{m.group(3)}' if m else ''

            page_articles.append({'title': text, 'url': full_url, 'date': d})

        print(f'  Page {page}: {len(page_articles)} found')

        if not page_articles:
            break

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
            title = re.sub(r'\s*[-|]\s*Microsoft Azure Quantum Blog\s*$', '', title).strip()

        # Date from meta
        d = ''
        pub_meta = soup.find('meta', attrs={'name': 'awa-publishedDate'})
        if pub_meta:
            ds = pub_meta.get('content', '')  # YYYYMMDD
            if len(ds) == 8:
                d = f'{ds[:4]}-{ds[4:6]}-{ds[6:8]}'

        # Fallback date from URL
        if not d:
            m = URL_DATE_RE.search(url)
            if m:
                d = f'{m.group(1)}-{m.group(2)}-{m.group(3)}'

        # Content from main
        main = soup.find('main')
        content = main.get_text(separator='\n', strip=True) if main else soup.get_text(separator='\n', strip=True)

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
            print(f'  [{pub_date}] {best_title[:80]}')
        except sqlite3.IntegrityError:
            pass

    conn.close()
    print(f'[OK] {new_count} new articles from {SOURCE_NAME}')
