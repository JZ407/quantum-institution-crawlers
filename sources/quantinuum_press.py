"""Crawl Quantinuum Press Releases via hash pagination."""
import sys, os, re, time, requests
from datetime import datetime
from bs4 import BeautifulSoup
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.base import BaseCrawler
from core.llm import get_llm

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
BASE = 'https://www.quantinuum.com/news/news'

def crawl_press_releases():
    """Custom crawler for Quantinuum press releases using hash pagination."""
    s = requests.Session()

    # Get the press release hash from page 1
    resp = s.get(BASE, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, 'html.parser')

    pr_hash = None
    for a in soup.find_all('a', href=True):
        m = re.search(r'\?([a-f0-9]+)_page=2', a['href'])
        if not m or 'View More' not in a.get_text(strip=True):
            continue
        h = m.group(1)
        # Verify this hash has press release articles
        test_url = f'{BASE}?{h}_page=1'
        tr = s.get(test_url, headers=HEADERS, timeout=15)
        ts = BeautifulSoup(tr.text, 'html.parser')
        count = len([a2 for a2 in ts.find_all('a', href=True) if '/press-releases/' in a2.get('href', '')])
        if count > 3:
            pr_hash = h
            break

    if not pr_hash:
        print('Could not find press release hash')
        return []

    print(f'Using hash: {pr_hash}')

    articles = []
    seen_urls = set()
    for page in range(1, 30):
        url = f'{BASE}?{pr_hash}_page={page}'
        resp = s.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')

        page_new = 0
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/press-releases/' not in href:
                continue
            if href.startswith('/'):
                full = 'https://www.quantinuum.com' + href
            else:
                full = href
            if full in seen_urls:
                continue
            seen_urls.add(full)

            # Extract date + real title from raw card text
            d = ''
            real_title = ''
            parent = a.parent
            for _ in range(4):
                if not parent:
                    break
                raw = parent.get_text()
                m = re.search(r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})\s*([A-Z][^.]{20,200}?)Read our announcement', raw)
                if m:
                    try:
                        d = datetime.strptime(m.group(1).replace(',', ''), '%B %d %Y').strftime('%Y-%m-%d')
                    except ValueError:
                        pass
                    real_title = m.group(2).strip()
                    break
                parent = parent.parent

            if real_title and d:
                articles.append({'title': real_title, 'url': full, 'date': d})
                page_new += 1

        if page_new == 0:
            break
        time.sleep(0.3)

    return articles


if __name__ == '__main__':
    print('[CRAWL] Quantinuum Press: custom hash pagination')
    articles = crawl_press_releases()
    print(f'  Found {len(articles)} articles')

    crawler = BaseCrawler({'name': 'Quantinuum Press', 'type': 'enterprise',
                            'url': BASE, 'url_pattern': '/press-releases/'})
    crawler.connect_db()
    crawler.set_llm(get_llm())

    new_count = 0
    for art in articles:
        if not crawler.conn:
            break
        c = crawler.conn.cursor()
        c.execute('SELECT id FROM articles WHERE url=?', (art['url'],))
        if c.fetchone():
            continue

        detail = crawler._fetch_detail(art['url'])
        content = detail['content']
        pub_date = detail['date'] or art['date']
        best_title = art['title']
        if detail.get('title') and len(detail['title']) > len(best_title):
            best_title = detail['title']

        summary = content[:300].strip() if content else ''
        title_cn = ''
        if best_title and crawler.client and not any('一' <= c <= '鿿' for c in best_title[:20]):
            try:
                tn_msg = [
                    {'role': 'system', 'content': '将以下英文新闻标题翻译为中文。只输出中文，不要解释。'},
                    {'role': 'user', 'content': best_title},
                ]
                title_cn = crawler.client.chat(tn_msg).strip()
                if len(title_cn) > 200:
                    title_cn = title_cn[:200]
            except Exception:
                pass

        try:
            crawler.conn.execute('''INSERT INTO articles (title, content, url, source, publish_date, summary, title_cn)
                                  VALUES (?, ?, ?, ?, ?, ?, ?)''',
                               (best_title, content, art['url'], 'Quantinuum Press', pub_date, summary, title_cn))
            crawler.conn.commit()
            new_count += 1
        except Exception:
            pass

    crawler.conn.close()
    print(f'[OK] {new_count} new articles from Quantinuum Press')
