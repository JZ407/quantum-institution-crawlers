"""OQC news + resources crawler - standalone, WordPress sitemap."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SITEMAP_POSTS = 'https://oqc.tech/wp-sitemap-posts-post-1.xml'
DATE_RE = re.compile(
    r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})'
)


def crawl_sitemap():
    resp = requests.get(SITEMAP_POSTS, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, 'xml')

    newsroom = []
    resources = []

    for u in soup.find_all('url'):
        loc = u.find('loc')
        lm = u.find('lastmod')
        if not loc:
            continue
        url = loc.get_text(strip=True)
        lastmod = lm.get_text(strip=True)[:10] if lm else ''

        if '/company/newsroom/' in url:
            newsroom.append({'url': url, 'date': lastmod})
        elif '/resources/' in url:
            resources.append({'url': url, 'date': lastmod})

    return newsroom, resources


def fetch_detail(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'date': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Title from <title>
        title = ''
        if soup.title:
            title = re.sub(r'\s*[–\-|]\s*OQC\s*$', '', soup.title.string).strip()

        # Date from text
        d = ''
        m = DATE_RE.search(soup.get_text())
        if m:
            try:
                d = datetime.strptime(m.group(1), '%B %d, %Y').strftime('%Y-%m-%d')
            except ValueError:
                pass

        # Content from article or post-content
        article = soup.find('article')
        if article:
            content = article.get_text(separator='\n', strip=True)
        else:
            body = soup.find(class_=re.compile(r'post-content|entry-content'))
            content = body.get_text(separator='\n', strip=True) if body else soup.get_text(separator='\n', strip=True)

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
    print(f'[CRAWL] OQC: {SITEMAP_POSTS}')

    newsroom, resources = crawl_sitemap()
    print(f'  Newsroom: {len(newsroom)}, Resources: {len(resources)}, Total: {len(newsroom) + len(resources)}')

    conn = init_db()
    client = get_llm()

    print(f'\n--- OQC Newsroom ({len(newsroom)} URLs) ---')
    n_new = process_articles(newsroom, 'OQC Newsroom', conn, client)

    print(f'\n--- OQC Resources ({len(resources)} URLs) ---')
    r_new = process_articles(resources, 'OQC Resources', conn, client)

    conn.close()
    print(f'\n[OK] {n_new} newsroom + {r_new} resources = {n_new + r_new} total')
