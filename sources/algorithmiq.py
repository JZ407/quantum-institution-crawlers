"""Algorithmiq news crawler - sitemap-based, server-rendered HTML."""
import sys, os, re, json, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url, load_known_urls

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SITEMAP_URL = 'https://www.algorithmiq.fi/sitemap-0.xml'
SOURCE_NAME = 'Algorithmiq'

# Date patterns in article text: "11th May 2026", "May 11, 2026", etc.
DATE_PATTERNS = [
    (r'(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', '%d %B %Y'),
    (r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', '%B %d %Y'),
]

MONTH_MAP = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
}


def crawl_sitemap():
    """Extract all /news/ article URLs from sitemap."""
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=15)
    articles = []
    for m in re.finditer(
        r'<loc>https://algorithmiq\.fi/news/([^<]+)</loc>',
        resp.text
    ):
        slug = m.group(1)
        if slug == '':
            continue
        articles.append({'url': f'https://algorithmiq.fi/news/{slug}'})
    return articles


def parse_date(text):
    """Extract date from article body text."""
    for pat, fmt in DATE_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            groups = m.groups()
            if fmt == '%d %B %Y':
                day, month, year = groups
                return f'{year}-{MONTH_MAP.get(month.lower(), 1):02d}-{int(day):02d}'
            elif fmt == '%B %d %Y':
                month, day, year = groups
                return f'{year}-{MONTH_MAP.get(month.lower(), 1):02d}-{int(day):02d}'
    return ''


def fetch_detail(url):
    """Fetch and parse a news article detail page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'date': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Title from <title> tag (strip site name)
        title_tag = soup.find('title')
        title = ''
        if title_tag:
            raw = title_tag.get_text(strip=True)
            # Remove " - Algorithmiq" suffix
            title = re.sub(r'\s*[-–|]\s*Algorithmiq\s*$', '', raw).strip()

        # Content from <article> tag
        article = soup.find('article')
        content = ''
        if article:
            # Extract text from all sections
            sections = article.find_all('section')
            texts = []
            for sec in sections:
                t = sec.get_text(separator='\n', strip=True)
                if t:
                    texts.append(t)
            content = '\n\n'.join(texts)
        else:
            content = soup.get_text(separator='\n', strip=True)

        # Date from content body
        d = parse_date(content)

        return {'content': content, 'date': d, 'title': title}
    except Exception as e:
        print(f'  [ERR] fetch_detail: {e}')
        return {'content': '', 'date': '', 'title': ''}


if __name__ == '__main__':
    print(f'[CRAWL] {SOURCE_NAME}: {SITEMAP_URL}')

    articles = crawl_sitemap()
    print(f'  Found {len(articles)} news articles')

    conn = init_db()
    known_urls = load_known_urls(conn, SOURCE_NAME)
    client = get_llm()
    new_count = 0

    for art in articles:
        if art['url'] in known_urls:
            continue

        detail = fetch_detail(art['url'])
        content = detail['content']
        pub_date = detail['date']
        title = detail['title']
        if not title:
            title = art['url'].rstrip('/').split('/')[-1].replace('-', ' ').title()

        summary = content[:300].strip() if content else ''

        # Translate title to Chinese
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
            print(f'  [{pub_date}] {title[:90]}')
        except sqlite3.IntegrityError:
            pass

        time.sleep(0.2)

    conn.close()
    print(f'[OK] {new_count} new articles from {SOURCE_NAME}')
