"""QunaSys news crawler - standalone, HTML listing with page/N pagination."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SOURCE_NAME_JP = 'QunaSys News'
SOURCE_NAME_EN = 'QunaSys News (EN)'
DATE_RE = re.compile(r'(\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2})')


def crawl_listing(base_url, url_prefix):
    """Crawl HTML listing with page/N pagination."""
    s = requests.Session()
    articles = []
    seen = set()

    for page in range(1, 30):
        if page == 1:
            url = base_url
        else:
            url = f'{base_url}page/{page}'

        resp = s.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            break
        soup = BeautifulSoup(resp.text, 'html.parser')

        page_articles = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if url_prefix not in href or len(text) < 5 or 'Read Article' in text or '記事を読む' in text:
                continue

            full = 'https://qunasys.com' + href if href.startswith('/') else href
            if full in seen:
                continue
            seen.add(full)

            # Find date from parent context
            parent = a.parent
            d = ''
            for _ in range(5):
                if not parent:
                    break
                ctx = parent.get_text(strip=True)
                m = DATE_RE.search(ctx)
                if m:
                    ds = m.group(1).replace('.', '-').replace('/', '-')
                    if len(ds) == 9:  # YYYY-M-D → YYYY-MM-DD
                        parts = ds.split('-')
                        ds = f'{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}'
                    d = ds
                    break
                parent = parent.parent

            page_articles.append({'title': text, 'url': full, 'date': d})

        print(f'  Page {page}: {len(page_articles)} found')
        if not page_articles:
            break
        articles.extend(page_articles)
        time.sleep(0.2)

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
            title = re.sub(r'\s*[-–|]\s*QunaSys\s*$', '', title).strip()

        # Date from text
        d = ''
        m = DATE_RE.search(soup.get_text())
        if m:
            ds = m.group(1).replace('.', '-').replace('/', '-')
            parts = ds.split('-')
            d = f'{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}'

        # Content
        main = soup.find('main')
        article = soup.find('article')
        if article:
            content = article.get_text(separator='\n', strip=True)
        elif main:
            content = main.get_text(separator='\n', strip=True)
        else:
            content = soup.get_text(separator='\n', strip=True)

        return {'content': content, 'date': d, 'title': title}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


def process_articles(articles, source_name, conn, client):
    new_count = 0
    for art in articles:
        if not is_new_url(conn, art['url']):
            continue

        detail = fetch_detail(art['url'])
        content = detail['content']
        pub_date = detail['date'] or art['date']
        title = detail['title'] or art['title']

        summary = content[:300].strip() if content else ''

        title_cn = ''
        if title and client:
            try:
                tn_msg = [
                    {'role': 'system', 'content': '将以下新闻标题翻译为中文。只输出中文，不要解释。'},
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
                (title, content, art['url'], source_name, pub_date, summary, title_cn),
            )
            conn.commit()
            new_count += 1
            print(f'  [{pub_date}] {title[:80]}')
        except sqlite3.IntegrityError:
            pass

        time.sleep(0.15)

    return new_count


if __name__ == '__main__':
    print('[CRAWL] QunaSys: Japanese + English news')

    jp_articles = crawl_listing('https://qunasys.com/news/', '/news/posts/')
    en_articles = crawl_listing('https://qunasys.com/en/news/', '/en/news/posts/')
    print(f'  JP: {len(jp_articles)}, EN: {len(en_articles)}, Total: {len(jp_articles) + len(en_articles)}')

    conn = init_db()
    client = get_llm()

    print(f'\n--- QunaSys JP ({len(jp_articles)} URLs) ---')
    jp_new = process_articles(jp_articles, SOURCE_NAME_JP, conn, client)

    print(f'\n--- QunaSys EN ({len(en_articles)} URLs) ---')
    en_new = process_articles(en_articles, SOURCE_NAME_EN, conn, client)

    conn.close()
    print(f'\n[OK] {jp_new} JP + {en_new} EN = {jp_new + en_new} total')
