"""NVIDIA Quantum blog crawler - standalone, Atom feed."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url, load_known_urls

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
FEED_URL = 'https://developer.nvidia.com/blog/tag/quantum-computing/feed/'
SOURCE_NAME = 'NVIDIA Quantum'


def crawl_feed():
    resp = requests.get(FEED_URL, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, 'xml')
    entries = soup.find_all('entry')

    articles = []
    for entry in entries:
        title = entry.find('title').get_text(strip=True) if entry.find('title') else ''
        link = entry.find('link', href=True)
        url = link['href'] if link else ''
        pub = entry.find('published')
        d = ''
        if pub:
            try:
                d = pub.get_text(strip=True)[:10]  # YYYY-MM-DD
            except (ValueError, IndexError):
                pass

        if title and url:
            articles.append({'title': title, 'url': url, 'date': d})

    return articles


def fetch_detail(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'date': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Title
        title = ''
        og = soup.find('meta', property='og:title')
        if og:
            title = og.get('content', '').replace(' | NVIDIA Technical Blog', '').strip()

        # Date
        d = ''
        for meta in soup.find_all('meta'):
            name = meta.get('name', '')
            prop = meta.get('property', '')
            if 'date' in (name + prop).lower():
                content = meta.get('content', '')
                if content:
                    d = content[:10]
                    break

        # Content
        article = soup.find('article')
        if article:
            content = article.get_text(separator='\n', strip=True)
        else:
            main = soup.find('main')
            content = main.get_text(separator='\n', strip=True) if main else soup.get_text(separator='\n', strip=True)

        return {'content': content, 'date': d, 'title': title}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


if __name__ == '__main__':
    print(f'[CRAWL] {SOURCE_NAME}: {FEED_URL}')

    articles = crawl_feed()
    print(f'  Found {len(articles)} articles in feed')

    conn = init_db()
    # Load known URLs for fast dedup (in-memory instead of per-URL SQL query)
    known_urls = load_known_urls(conn, 'NVIDIA Quantum')
    client = get_llm()
    new_count = 0

    for art in articles:
        if art['url'] in known_urls:
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
