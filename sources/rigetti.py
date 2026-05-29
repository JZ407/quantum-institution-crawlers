"""Rigetti news + research crawler - standalone, sitemap-based."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SITEMAP_INDEX = 'https://www.rigetti.com/sitemaps-1-sitemap.xml'
SOURCE_NAME_NEWS = 'Rigetti News'
SOURCE_NAME_RESEARCH = 'Rigetti Research'


def crawl_sitemaps():
    """Parse sitemap index and sub-sitemaps for news and research URLs."""
    resp = requests.get(SITEMAP_INDEX, headers=HEADERS, timeout=15)
    sub_urls = {'news': '', 'research': ''}

    for m in re.finditer(r'<loc>(.*?)</loc>', resp.text):
        url = m.group(1)
        if 'section-news' in url:
            sub_urls['news'] = url
        elif 'section-research' in url:
            sub_urls['research'] = url

    articles = {'news': [], 'research': []}

    for key in ('news', 'research'):
        if not sub_urls[key]:
            continue
        resp = requests.get(sub_urls[key], headers=HEADERS, timeout=15)

        for block in re.findall(r'<url>(.*?)</url>', resp.text, re.DOTALL):
            loc = re.search(r'<loc>(.*?)</loc>', block)
            lm = re.search(r'<lastmod>(.*?)</lastmod>', block)
            if not loc:
                continue
            url = loc.group(1)
            # Skip category/index pages
            path = url.replace('https://www.rigetti.com', '')
            if path.count('/') <= 1:
                continue
            lastmod = lm.group(1)[:10] if lm else ''
            articles[key].append({'url': url, 'date': lastmod})

    return articles['news'], articles['research']


def fetch_detail(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Title from h1 or og:title
        title = ''
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)
        if not title:
            og = soup.find('meta', property='og:title')
            if og:
                title = og.get('content', '').strip()

        # Content - limited due to JS rendering, get whatever is available
        body = soup.find('body')
        content = body.get_text(separator='\n', strip=True) if body else soup.get_text(separator='\n', strip=True)

        return {'content': content, 'title': title}
    except Exception:
        return {'content': '', 'title': ''}


def process_articles(articles, source_name, conn, client):
    new_count = 0
    for art in articles:
        if not is_new_url(conn, art['url']):
            continue

        detail = fetch_detail(art['url'])
        content = detail['content']
        title = detail['title']
        if not title:
            title = art['url'].rstrip('/').split('/')[-1].replace('-', ' ').title()
        pub_date = art['date']

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
    print(f'[CRAWL] Rigetti: {SITEMAP_INDEX}')

    news_articles, research_articles = crawl_sitemaps()
    print(f'  News: {len(news_articles)}, Research: {len(research_articles)}, Total: {len(news_articles) + len(research_articles)}')

    conn = init_db()
    client = get_llm()

    print(f'\n--- Rigetti News ({len(news_articles)} URLs) ---')
    news_new = process_articles(news_articles, SOURCE_NAME_NEWS, conn, client)

    print(f'\n--- Rigetti Research ({len(research_articles)} URLs) ---')
    research_new = process_articles(research_articles, SOURCE_NAME_RESEARCH, conn, client)

    conn.close()
    print(f'\n[OK] {news_new} news + {research_new} research = {news_new + research_new} total')
