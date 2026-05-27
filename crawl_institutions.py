"""
Crawl news from top quantum institutions. Stores in SQLite with quantum-relevance filter.
"""
import sys, os, time, re, json, sqlite3, argparse
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, 'D:/Claude_code/rag_system/rag_system')
from llm_client import LLMClient
import yaml

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'institutions.db')
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

SOURCES = [
    {
        'name': 'IBM Quantum',
        'type': 'enterprise',
        'url': 'https://www.ibm.com/quantum/blog',
        'article_selector': 'a',
        'url_pattern': '/quantum/blog/',
        'quantum_native': True,
        'max_pages': 5,
        'page_url_template': '?page={n}',
    },
    {
        'name': 'Quantinuum',
        'type': 'enterprise',
        'url': 'https://www.quantinuum.com/news/blog',
        'article_selector': 'a',
        'url_pattern': '/blog/',
        'quantum_native': True,
        'max_pages': 5,
    },
    {
        'name': 'Google Quantum AI',
        'type': 'sitemap',
        'url': 'https://blog.google/en-us/sitemap.xml',
        'url_pattern': 'quantum',
        'quantum_native': True,
    },
    {
        'name': 'Microsoft Azure Quantum',
        'type': 'enterprise',
        'url': 'https://cloudblogs.microsoft.com/quantum/',
        'article_selector': 'a',
        'url_pattern': '/quantum/',
        'quantum_native': True,
    },
    {
        'name': 'NVIDIA Quantum',
        'type': 'atom',
        'url': 'https://developer.nvidia.com/blog/tag/quantum-computing/feed/',
        'article_selector': 'a',
        'url_pattern': '/blog/',
        'quantum_native': True,
    },
    # ---- Tier 2 ----
    {
        'name': 'IonQ',
        'type': 'sitemap',
        'url': 'https://www.ionq.com/sitemap.xml',
        'url_pattern': 'quantum',
        'quantum_native': True,
    },
    {
        'name': 'Rigetti',
        'type': 'sitemap',
        'url': 'https://www.rigetti.com/sitemaps-1-sitemap.xml',
        'url_pattern': 'quantum',
        'quantum_native': True,
    },
    {
        'name': 'PsiQuantum',
        'type': 'sitemap',
        'url': 'https://www.psiquantum.com/sitemap.xml',
        'url_pattern': 'quantum',
        'quantum_native': True,
    },
    {
        'name': 'Atom Computing',
        'type': 'enterprise',
        'url': 'https://atom-computing.com/news',
        'url_pattern': '/news/',
        'quantum_native': True,
    },
    {
        'name': 'QunaSys',
        'type': 'enterprise',
        'url': 'https://qunasys.com/news',
        'url_pattern': '/news/',
        'quantum_native': True,
    },
    {
        'name': 'QuEra',
        'type': 'enterprise',
        'url': 'https://www.quera.com/news',
        'url_pattern': '/news/',
        'quantum_native': True,
    },
    {
        'name': 'OQC',
        'type': 'enterprise',
        'url': 'https://oqc.tech/company/newsroom/',
        'url_pattern': '/company/newsroom/',
        'quantum_native': True,
        'max_pages': 8,
    },
    {
        'name': 'Q-CTRL',
        'type': 'sitemap',
        'url': 'https://q-ctrl.com/sitemap.xml',
        'url_pattern': 'quantum',
        'quantum_native': True,
    },
    {
        'name': 'Classiq',
        'type': 'enterprise',
        'url': 'https://www.classiq.io/blog',
        'url_pattern': '/blog/',
        'quantum_native': True,
    },
]


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT,
        url TEXT UNIQUE,
        source TEXT,
        publish_date TEXT,
        tags TEXT,
        summary TEXT,
        summary_cn TEXT,
        fetch_status TEXT DEFAULT 'listed',
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    try:
        c.execute('ALTER TABLE articles ADD COLUMN summary_cn TEXT')
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def get_llm():
    cfg_path = 'D:/Claude_code/rag_system/config.yaml'
    cfg = yaml.safe_load(open(cfg_path, encoding='utf-8'))['llm']
    return LLMClient(provider='openai', api_key=cfg['api_key'], api_base=cfg['api_base'],
                     model=cfg['model'], max_tokens=2048, timeout=120)


def _parse_atom_date(date_str: str) -> str:
    """Parse Atom/RSS date formats to YYYY-MM-DD."""
    if not date_str:
        return ''
    # Try ISO 8601: 2026-05-27T12:00:00Z
    m = re.match(r'(\d{4}-\d{2}-\d{2})', date_str)
    if m:
        return m.group(1)
    # Try RFC 2822: Wed, 21 Jan 2026 12:00:00 +0000
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    # Try common formats
    for fmt in ['%d %b %Y', '%B %d, %Y', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ']:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    return ''


def crawl_atom(source: dict) -> list:
    """Crawl an Atom/RSS feed, return [{title, url, date}]."""
    try:
        resp = requests.get(source['url'], headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(f'  HTTP {resp.status_code}')
            return []
        soup = BeautifulSoup(resp.text, 'xml')
        articles = []
        keyword = source['url_pattern'].lower()
        for entry in soup.find_all(['entry', 'item']):
            title = (entry.find('title') or {}).text if entry.find('title') else ''
            link_el = entry.find('link')
            link = ''
            if link_el:
                link = link_el.get('href', '') or link_el.text or ''
            pub_el = entry.find('published') or entry.find('pubDate')
            pub_raw = pub_el.text if pub_el else ''
            pub = _parse_atom_date(pub_raw)
            if link and keyword in link.lower():
                articles.append({
                    'title': title.strip(),
                    'url': link.strip(),
                    'date': pub[:10] if pub else '',
                })
        return articles
    except Exception as e:
        print(f'  Error: {e}')
        return []


def _is_article_url(url: str) -> bool:
    """Check if a URL looks like an article (not a product/nav page)."""
    path = urlparse(url).path.lower()
    # Must contain a news/blog/article segment
    article_patterns = ['/blog/', '/news/', '/press/', '/insight/', '/article/',
                        '/post/', '/event/', '/story/', '/learn/', '/resource/',
                        '/research/', '/news-import/']
    for p in article_patterns:
        if p in path:
            # Exclude bare category/tag pages
            if not path.rstrip('/').endswith(('/category', '/tag', '/author', '/page')):
                return True
    # Also match if it ends with a pattern word (e.g., /news, /research)
    endings = ['/blog', '/news', '/press', '/insight', '/research']
    for e in endings:
        if path.rstrip('/').endswith(e):
            return True
    return False


def crawl_sitemap(source: dict) -> list:
    """Crawl a sitemap XML (or sitemap index), filter by url_pattern + article path.
    Returns [{title, url, date}]."""
    try:
        resp = requests.get(source['url'], headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(f'  HTTP {resp.status_code}')
            return []
        soup = BeautifulSoup(resp.text, 'xml')
        keyword = source['url_pattern'].lower()

        # If it's a sitemap index, find the news/blog sub-sitemap
        sub_sitemaps = soup.find_all('sitemap')
        if sub_sitemaps:
            news_sm = None
            for sm in sub_sitemaps:
                loc = (sm.find('loc') or {}).text if sm.find('loc') else ''
                if re.search(r'(blog|news|press|article|post)', loc, re.I):
                    news_sm = loc
                    break
            if news_sm:
                resp = requests.get(news_sm, headers=HEADERS, timeout=30)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, 'xml')
                else:
                    return []

        articles = []
        for url in soup.find_all('url'):
            loc = (url.find('loc') or {}).text if url.find('loc') else ''
            lastmod = (url.find('lastmod') or {}).text if url.find('lastmod') else ''
            if keyword in loc.lower() and _is_article_url(loc):
                slug = loc.rstrip('/').rsplit('/', 1)[-1].replace('-', ' ')
                title = ' '.join(w[0].upper() + w[1:] if w else w for w in slug.split())
                articles.append({'title': title, 'url': loc, 'date': lastmod[:10] if lastmod else ''})
        return articles
    except Exception as e:
        print(f'  Error: {e}')
        return []


def _extract_articles_from_soup(soup, source: dict, base_url: str) -> list:
    """Extract articles from a BeautifulSoup page."""
    articles = []
    for a in soup.find_all('a', href=True):
        title = a.get_text(strip=True)
        href = a['href']
        if len(title) < 15:
            continue
        if href.startswith('/'):
            href = requests.compat.urljoin(base_url, href)
        elif not href.startswith('http'):
            continue
        if source['url_pattern'] not in href:
            continue
        d = ''
        parent = a.parent
        for _ in range(4):
            if not parent:
                break
            for el in parent.find_all(['time', 'span', 'div', 'p']):
                text = el.get_text(strip=True)
                m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
                if m:
                    d = m.group(1)
                    break
                m = re.search(r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})', text)
                if m:
                    try:
                        d = datetime.strptime(m.group(1), '%d %b %Y').strftime('%Y-%m-%d')
                    except ValueError:
                        pass
                    break
                m = re.search(r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})', text)
                if m:
                    try:
                        d = datetime.strptime(m.group(1).replace(',', ''), '%B %d %Y').strftime('%Y-%m-%d')
                    except ValueError:
                        pass
                    break
            if d:
                break
            parent = parent.parent
        articles.append({'title': title, 'url': href, 'date': d})
    return articles


def _find_next_page(soup, base_url: str, current_page: int, page_template: str = '') -> str:
    """Find next page URL from pagination links or URL template."""
    next_page = current_page + 1
    # Priority 0: explicit URL template (for JS-button pagination like IBM)
    if page_template:
        return base_url + page_template.format(n=next_page)
    # Priority 1: explicit _page=N (Quantinuum/OQC) or page=N query params
    for a in soup.find_all('a', href=True):
        href = a['href']
        text = a.get_text(strip=True).lower()
        # _page=N with navigation text (view more/next/older) or bare number
        if re.search(rf'_page={next_page}\b', href):
            if any(k in text for k in ['view more', 'next', 'older']) or text.strip() == str(next_page):
                if href.startswith('?'):
                    return base_url + href
                if href.startswith('/'):
                    return requests.compat.urljoin(base_url, href)
                if href.startswith('http'):
                    return href
                return requests.compat.urljoin(base_url, href)
        # page=N or data-page=N with numbered links
        if re.search(rf'[?&]page={next_page}\b', href) or a.get('data-page') == str(next_page):
            if text.strip() == str(next_page) or 'page' in text:
                if href.startswith('?') or href.startswith('/'):
                    return requests.compat.urljoin(base_url, href)
                if href.startswith('http'):
                    return href
                return requests.compat.urljoin(base_url, href)
    # Priority 2: generic "next" text with page=N in href
    for a in soup.find_all('a', href=True):
        text = a.get_text(strip=True).lower()
        href = a['href']
        if any(k in text for k in ['next', 'older posts', 'older entries']):
            if re.search(r'[?&]page=\d+', href):
                if href.startswith('?') or href.startswith('/'):
                    return requests.compat.urljoin(base_url, href)
                if href.startswith('http'):
                    return href
                return requests.compat.urljoin(base_url, href)
    # Priority 3: rel=next
    link = soup.find('link', rel='next')
    if link and link.get('href'):
        href = link['href']
        if href.startswith('/'):
            return requests.compat.urljoin(base_url, href)
        return href
    return ''


def crawl_listing(source: dict) -> list:
    """Crawl listing page with optional pagination. Returns [{title, url, date}]"""
    try:
        base_url = source['url']
        resp = requests.get(base_url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f'  HTTP {resp.status_code}')
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        articles = _extract_articles_from_soup(soup, source, base_url)

        # Pagination
        max_pages = source.get('max_pages', 5)
        seen_urls = {a['url'] for a in articles}
        page_num = 1
        while page_num < max_pages:
            next_url = _find_next_page(soup, base_url, page_num, source.get('page_url_template', ''))
            if not next_url or next_url == base_url:
                break
            page_num += 1
            time.sleep(0.5)
            resp = requests.get(next_url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, 'html.parser')
            new_articles = _extract_articles_from_soup(soup, source, next_url)
            added = 0
            for art in new_articles:
                if art['url'] not in seen_urls:
                    seen_urls.add(art['url'])
                    articles.append(art)
                    added += 1
            if added == 0:
                break
        return articles
    except Exception as e:
        print(f'  Error: {e}')
        return []


def _extract_date(soup, text: str) -> str:
    """Extract publish date from meta, time, JSON-LD, or regex."""
    # 1. Meta tags
    for meta in soup.find_all('meta'):
        prop = (meta.get('property', '') or meta.get('name', '')).lower()
        if any(k in prop for k in ['date', 'published', 'modified', 'article:published']):
            d = meta.get('content', '')[:10]
            if re.match(r'\d{4}-\d{2}-\d{2}', d):
                return d

    # 2. <time> element
    t = soup.find('time')
    if t:
        dt = t.get('datetime', '') or t.get_text(strip=True)
        if dt:
            return dt[:10] if re.match(r'\d{4}-\d{2}-\d{2}', dt[:10]) else dt

    # 3. JSON-LD structured data
    import json as _json
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = _json.loads(script.string)
            if isinstance(data, dict):
                d = data.get('datePublished') or data.get('dateModified') or ''
            elif isinstance(data, list):
                d = data[0].get('datePublished', '') if data else ''
            else:
                d = ''
            if d and re.match(r'\d{4}-\d{2}-\d{2}', str(d)[:10]):
                return str(d)[:10]
        except Exception:
            pass

    # 4. Regex on visible text (search first 2000 chars for date patterns near top)
    head_text = text[:2000]
    # "16 Mar 2026" or "March 16, 2026"
    m = re.search(r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})', head_text)
    if m:
        try:
            dt = datetime.strptime(m.group(1), '%d %b %Y')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass
    # "March 16, 2026"
    m = re.search(r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})', head_text)
    if m:
        try:
            dt = datetime.strptime(m.group(1).replace(',', ''), '%B %d %Y')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass
    # "2026-05-16"
    m = re.search(r'(\d{4}-\d{2}-\d{2})', head_text)
    if m:
        return m.group(1)

    return ''


def _extract_page_title(soup) -> str:
    """Extract real page title from og/meta/h1."""
    for meta in soup.find_all('meta'):
        if meta.get('property', '') == 'og:title':
            t = meta.get('content', '').strip()
            if t:
                return t
    for meta in soup.find_all('meta'):
        if meta.get('name', '') in ('title', 'dc.title'):
            t = meta.get('content', '').strip()
            if t:
                return t
    h1 = soup.find('h1')
    if h1:
        t = h1.get_text(strip=True)
        if t:
            return t
    if soup.title:
        return soup.title.string.strip() if soup.title.string else ''
    return ''


def _extract_body(soup) -> str:
    """Extract clean article body text from common content containers."""
    # Try semantic article containers first
    for selector in ['article', '[role=main]', 'main',
                     '[class*=article-body]', '[class*=post-body]',
                     '[class*=entry-content]', '[class*=blog-content]',
                     '[class*=article-content]', '[class*=post-content]']:
        el = soup.select_one(selector)
        if el:
            # Remove nested nav/footer within article
            for tag in el.find_all(['script', 'style', 'nav', 'footer', 'aside',
                                     'header', '.sidebar', '.related-posts',
                                     '.comments', '.social-share']):
                tag.decompose()
            text = el.get_text(separator='\n', strip=True)
            if len(text) > 300:
                return text
    # Fallback: remove noise elements from full page
    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'header',
                               'aside', '.sidebar', '.menu', '.navigation',
                               '.related', '.comments', '.footer']):
        tag.decompose()
    text = soup.get_text(separator='\n', strip=True)
    # Clean up: remove short lines, collapse whitespace
    lines = [l.strip() for l in text.split('\n') if len(l.strip()) > 30]
    return '\n'.join(lines)


def fetch_detail(url: str) -> dict:
    """Fetch article detail page content, date, and real title."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'date': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')
        d = _extract_date(soup, '')
        t = _extract_page_title(soup)
        body = _extract_body(soup)
        # Keep up to 5000 chars, prefer article body over raw page text
        content = body[:5000] if len(body) > 5000 else body
        return {'content': content, 'date': d, 'title': t}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


def filter_quantum_llm(articles: list, source_name: str, client) -> list:
    """LLM check: is this article quantum-related?"""
    if not articles:
        return []
    lines = [
        f"以下是{source_name}的{len(articles)}篇文章标题。请筛选出与量子科技相关的文章。",
        "输出格式：序号|1(相关)或0(不相关)|量子(如是)。不要解释。\n"
    ]
    for i, a in enumerate(articles, 1):
        lines.append(f"{i}|{a['title'][:100]}")
    messages = [
        {"role": "system", "content": "你是量子科技专家。只输出要求的格式。"},
        {"role": "user", "content": "\n".join(lines)},
    ]
    try:
        resp = client.chat(messages)
        keep = set()
        for line in resp.strip().split('\n'):
            if '|' not in line:
                continue
            parts = line.split('|')
            try:
                idx = int(parts[0].strip()) - 1
                val = parts[1].strip()
                if '1' in val and 0 <= idx < len(articles):
                    keep.add(idx)
            except ValueError:
                continue
        return [articles[i] for i in keep]
    except Exception:
        return articles  # keep all if LLM fails


def crawl_one_source(src: dict, conn, client=None) -> int:
    """Crawl a single source. Returns number of new articles saved."""
    c = conn.cursor()
    s_name = src['name']
    s_url = src['url']
    print(f'\n[CRAWL] {s_name}: {s_url}')

    if src.get('type') == 'sitemap':
        articles = crawl_sitemap(src)
    elif src.get('type') == 'atom':
        articles = crawl_atom(src)
    else:
        articles = crawl_listing(src)
    print(f'  Found {len(articles)} articles')

    if src.get('quantum_native'):
        relevant = articles
        print(f'  Quantum-native (all {len(articles)} kept)')
    elif client:
        relevant = filter_quantum_llm(articles, s_name, client)
        print(f'  Quantum-related: {len(relevant)}')
    else:
        relevant = articles

    new_count = 0
    for art in relevant:
        c.execute('SELECT id FROM articles WHERE url=?', (art['url'],))
        if c.fetchone():
            continue

        detail = fetch_detail(art['url'])
        content = detail['content']
        pub_date = detail['date'] or art['date']
        best_title = art['title']
        if detail.get('title') and len(detail['title']) > len(best_title):
            best_title = detail['title']

        summary = content[:300].strip() if content else ''
        summary_cn = ''
        if content and client:
            try:
                cn_msg = [
                    {"role": "system", "content": "你是量子科技翻译专家。请将以下英文文章内容总结为一句话中文摘要（100字以内）。只输出中文，不要解释。"},
                    {"role": "user", "content": f"标题：{best_title}\n\n内容：{content[:2000]}"},
                ]
                summary_cn = client.chat(cn_msg).strip()
                if len(summary_cn) > 200:
                    summary_cn = summary_cn[:200]
            except Exception:
                pass

        try:
            c.execute('''INSERT INTO articles (title, content, url, source, publish_date, summary, summary_cn)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (best_title, content, art['url'], s_name, pub_date, summary, summary_cn))
            new_count += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    return new_count


def main():
    parser = argparse.ArgumentParser(description='Crawl quantum institution news')
    parser.add_argument('--source', '-s', type=str, help='Crawl a specific institution by name')
    parser.add_argument('--list', '-l', action='store_true', help='List all configured sources')
    parser.add_argument('--no-llm', action='store_true', help='Skip LLM Chinese summary generation')
    args = parser.parse_args()

    if args.list:
        for s in SOURCES:
            n, t, u = s['name'], s['type'], s['url']
            print(f'  {n:30s} | {t:10s} | {u}')
        return

    conn = init_db()
    client = None if args.no_llm else get_llm()
    total_new = 0

    if args.source:
        # Crawl specific institution (fuzzy match)
        matched = [s for s in SOURCES if args.source.lower() in s['name'].lower()]
        if not matched:
            print(f'No source matching "{args.source}". Use --list to see available sources.')
            conn.close()
            return
        for src in matched:
            total_new += crawl_one_source(src, conn, client)
    else:
        for src in SOURCES:
            total_new += crawl_one_source(src, conn, client)
            time.sleep(1)

    conn.close()
    print(f'\n[OK] {total_new} new articles saved to {DB_PATH}')


if __name__ == '__main__':
    main()
