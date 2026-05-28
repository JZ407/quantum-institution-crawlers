"""Quantinuum Blog crawler - standalone, HTML listing with View More pagination."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.extractors import extract_date, extract_page_title, extract_body
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
BASE = 'https://www.quantinuum.com/news/blog'
SOURCE_NAME = 'Quantinuum Blog'


def crawl_listing():
    """Crawl the blog listing with View More hash pagination."""
    s = requests.Session()
    resp = s.get(BASE, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, 'html.parser')

    # Find the pagination hash
    blog_hash = None
    for a in soup.find_all('a', href=True):
        m = re.search(r'\?([a-f0-9]+)_page=2', a['href'])
        if m and 'View More' in a.get_text(strip=True):
            blog_hash = m.group(1)
            break

    if not blog_hash:
        print('Could not find pagination hash')
        return []

    print(f'Hash: {blog_hash}')

    articles = []
    seen_urls = set()
    for page in range(1, 30):
        url = f'{BASE}?{blog_hash}_page={page}'
        resp = s.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')

        page_new = 0
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/blog/' not in href or len(a.get_text(strip=True)) < 5:
                continue
            if href.startswith('/'):
                full = 'https://www.quantinuum.com' + href
            else:
                full = href
            if full in seen_urls:
                continue
            seen_urls.add(full)

            # Extract date from card text
            d = ''
            real_title = ''
            parent = a.parent
            for _ in range(5):
                if not parent:
                    break
                raw = parent.get_text()
                m = re.search(r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})\s*([A-Z].{10,200}?)(?:Read our blogpost|Read more)', raw)
                if not m:
                    m = re.search(r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})', raw)
                    if m:
                        try:
                            d = datetime.strptime(m.group(1).replace(',', ''), '%B %d %Y').strftime('%Y-%m-%d')
                        except ValueError:
                            pass
                        # Title is the link text
                        real_title = a.get_text(strip=True)
                        if len(real_title) < 15:
                            real_title = ''
                        break
                else:
                    try:
                        d = datetime.strptime(m.group(1).replace(',', ''), '%B %d %Y').strftime('%Y-%m-%d')
                    except ValueError:
                        pass
                    real_title = m.group(2).strip()
                    break
                parent = parent.parent

            if real_title:
                articles.append({'title': real_title, 'url': full, 'date': d})
                page_new += 1

        if page_new == 0:
            break
        time.sleep(0.3)

    return articles


def fetch_detail(url):
    """Fetch article detail: content, date, title."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'date': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')
        body = extract_body(soup)
        full_text = soup.get_text(separator='\n', strip=True)
        content = body if body else full_text
        d = extract_date(soup, full_text)
        t = extract_page_title(soup)
        return {'content': content, 'date': d, 'title': t}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


if __name__ == '__main__':
    print(f'[CRAWL] {SOURCE_NAME}: {BASE}')

    articles = crawl_listing()
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
        best_title = art['title']
        if detail.get('title') and len(detail['title']) > len(best_title):
            best_title = detail['title']

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
            c.execute('''INSERT INTO articles (title, content, url, source, publish_date, summary, title_cn)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (best_title, content, art['url'], SOURCE_NAME, pub_date, summary, title_cn))
            conn.commit()
            new_count += 1
        except sqlite3.IntegrityError:
            pass

    conn.close()
    print(f'[OK] {new_count} new articles from {SOURCE_NAME}')
