"""IonQ news + blog crawler - standalone, sitemap-based."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SITEMAP_URL = 'https://www.ionq.com/sitemap.xml'
DATE_RE = re.compile(
    r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})'
)


def crawl_sitemap():
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    news_urls = set()
    blog_urls = set()

    for m in re.finditer(r'<loc>(https://www\.ionq\.com/[^<]+)</loc>', resp.text):
        url = m.group(1)
        if '/news/' in url and url.count('/') >= 4:
            news_urls.add(url)
        elif '/blog/' in url and url.count('/') >= 4:
            blog_urls.add(url)

    return sorted(news_urls), sorted(blog_urls)


def fetch_detail(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'date': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Title: try h1 first (news pages), fallback to og:title (blog pages)
        title = ''
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)
        if not title:
            og = soup.find('meta', property='og:title')
            if og:
                title = og.get('content', '')
                title = re.sub(r'^IonQ\s*\|\s*', '', title).strip()

        # Date from text
        d = ''
        m = DATE_RE.search(soup.get_text())
        if m:
            try:
                d = datetime.strptime(m.group(1), '%B %d, %Y').strftime('%Y-%m-%d')
            except ValueError:
                pass

        # Content from rich-text div
        rt = soup.find(class_=re.compile(r'rich-text'))
        content = rt.get_text(separator='\n', strip=True) if rt else soup.get_text(separator='\n', strip=True)

        return {'content': content, 'date': d, 'title': title}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


def process_urls(urls, source_name, conn, client):
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
                (title, content, url, source_name, pub_date, summary, title_cn),
            )
            conn.commit()
            new_count += 1
            print(f'  [{pub_date}] {title[:80]}')
        except sqlite3.IntegrityError:
            pass

        time.sleep(0.2)

    return new_count


if __name__ == '__main__':
    print(f'[CRAWL] IonQ: {SITEMAP_URL}')

    news_urls, blog_urls = crawl_sitemap()
    print(f'  News: {len(news_urls)}, Blog: {len(blog_urls)}, Total: {len(news_urls) + len(blog_urls)}')

    conn = init_db()
    client = get_llm()

    print(f'\n--- IonQ News ({len(news_urls)} URLs) ---')
    news_new = process_urls(news_urls, 'IonQ News', conn, client)

    print(f'\n--- IonQ Blog ({len(blog_urls)} URLs) ---')
    blog_new = process_urls(blog_urls, 'IonQ Blog', conn, client)

    conn.close()
    print(f'\n[OK] {news_new} news + {blog_new} blog = {news_new + blog_new} total')
