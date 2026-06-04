"""D-Wave press release + blog crawler - sitemap-based.

Sources:
  - /company/newsroom/press-release/ — 246 press releases
  - /learn/blog/posts/ — ~17 blog posts (sitemap) + dynamic listings
"""
import sys, os, re, time, random, sqlite3, requests
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
SITEMAP_URL = 'https://www.dwavequantum.com/sitemap.xml'

# Date formats: "June 01, 2026" or "April 8, 2026"
DATE_RE = re.compile(
    r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}'
)
MONTH_MAP = {
    'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5, 'June': 6,
    'July': 7, 'August': 8, 'September': 9, 'October': 10, 'November': 11, 'December': 12
}

# Stop markers: boilerplate sections that appear AFTER the article body.
# Content is trimmed from the first occurrence of any of these.
# Stop markers are applied ONLY when the line is a section header (short line
# or exact match), not when a company name appears in the middle of an article.
STOP_HEADERS = [
    'About D-Wave',
    'About D-Wave Systems Inc.',
    'Forward-Looking Statements',
    'Important Information About the Proposed Transaction',
    'No Offer or Solicitation',
    'Participants in Solicitation',
    'Media Contact:',
    'Investor Contact:',
    'Investor Relations Contact:',
    'Contacts',
    'Privacy Policy',
    'Terms of Use',
    'Cookie Policy',
    'Cautionary Note Regarding Forward-Looking',
    'Share',
    'Facebook',
    'Twitter',
    'LinkedIn',
]


def _polite_delay(min_s=1.0, max_s=3.0):
    time.sleep(random.uniform(min_s, max_s))


def crawl_sitemap():
    """Parse sitemap, extract press release + blog URLs with lastmod dates."""
    print(f"Fetching sitemap: {SITEMAP_URL}")
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        print(f"  ERROR: HTTP {resp.status_code}")
        return [], []

    press_urls = []
    blog_urls = []

    for block in re.finditer(r'<url>(.*?)</url>', resp.text, re.DOTALL):
        loc = re.search(r'<loc>([^<]+)</loc>', block.group(1))
        lastmod = re.search(r'<lastmod>([^<]+)</lastmod>', block.group(1))
        if not loc:
            continue

        url = loc.group(1)
        lm = lastmod.group(1) if lastmod else None

        # Press releases: /company/newsroom/press-release/slug/ (6+ slashes)
        if '/press-release/' in url and url.rstrip('/').count('/') >= 6:
            press_urls.append({'url': url, 'lastmod': lm})

        # Blog posts: /learn/blog/posts/slug/ (6+ slashes)
        elif '/learn/blog/posts/' in url and url.rstrip('/').count('/') >= 6:
            blog_urls.append({'url': url, 'lastmod': lm})

    print(f"  Press releases: {len(press_urls)}")
    print(f"  Blog posts: {len(blog_urls)}")
    return press_urls, blog_urls


def parse_date(date_str):
    """Parse Month DD, YYYY to YYYY-MM-DD."""
    if not date_str:
        return ''
    try:
        parts = date_str.split(' ')
        month = MONTH_MAP.get(parts[0], 1)
        day = int(parts[1].rstrip(','))
        year = int(parts[2])
        return f'{year}-{month:02d}-{day:02d}'
    except (ValueError, IndexError):
        return ''


def extract_date(soup):
    """Extract date from page. Try multiple strategies."""
    # Strategy 1: div with 't-news-section-text-item' or 't-dwave-blog-post' class
    for cls_pat in ['t-news-section-text-item', 't-dwave-blog-post']:
        div = soup.find('div', class_=lambda c: c and cls_pat in ' '.join(c) if isinstance(c, list) else False)
        if div:
            m = DATE_RE.search(div.get_text(strip=True))
            if m:
                return m.group(0)

    # Strategy 2: full body text
    body = soup.find('body')
    if body:
        for noise in body.find_all(['nav', 'header', 'footer', 'script', 'style']):
            noise.decompose()
        text = body.get_text()
        m = DATE_RE.search(text)
        if m:
            return m.group(0)

    return ''


def extract_content(soup):
    """Extract main article content by finding the text-richest section."""
    # Strategy 1: look for <section> or <div> with substantial text containing the article
    body = soup.find('body')
    if not body:
        return ''

    for noise in body.find_all(['nav', 'header', 'footer', 'script', 'style']):
        noise.decompose()

    lines = [l.strip() for l in body.get_text(separator='\n').split('\n') if l.strip()]

    # Find the start: skip header cruft (short lines, nav crumbs, menu items)
    # Look for the first substantial paragraph
    start = 0
    for i, l in enumerate(lines):
        if len(l) > 100 and not any(kw in l for kw in ['cookie', 'Cookie', 'Accept', 'Subscribe']):
            start = i
            break
    if start == 0:
        # If no long paragraph, start from beginning
        start = 0

    # Collect everything, but stop at first boilerplate section header.
    # Only match short lines (headers) or exact matches, to avoid matching
    # company names in the middle of article paragraphs.
    result = []
    for l in lines[start:]:
        is_stop = False
        for kw in STOP_HEADERS:
            if kw in l:
                # Short line = definitely a header. Long line = only stop if it STARTS with the kw.
                if len(l) < 80 or l.startswith(kw):
                    is_stop = True
                    break
        if is_stop:
            break
        result.append(l)

    # Post-process: remove duplicate title line at the start
    if result and len(result) > 1 and result[0] == result[1]:
        result = result[1:]

    return '\n'.join(result).strip()


def fetch_detail(url, source_type='press'):
    """Extract title, date, content from a D-Wave article page.

    Args:
        url: Article URL
        source_type: 'press' or 'blog'
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return {'title': '', 'date': '', 'content': '', 'url': url}
        soup = BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        print(f"    HTTP error: {e}")
        return {'title': '', 'date': '', 'content': '', 'url': url}

    # Title: h1 (press) or og:title (blog has no h1 in some templates)
    title = ''
    h1 = soup.find('h1')
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og_title = soup.find('meta', property='og:title')
        if og_title:
            title = og_title.get('content', '').strip()
    if not title:
        ttag = soup.find('title')
        if ttag:
            title = ttag.get_text(strip=True).split('|')[0].strip()

    # Date
    date_str = extract_date(soup)
    publish_date = parse_date(date_str)

    # Content
    content = extract_content(soup)

    return {
        'title': title,
        'date': publish_date,
        'content': content,
        'url': url
    }


def scrape_articles(articles, source_name, conn, llm):
    """Process a list of article entries: fetch detail, insert to DB."""
    stats = {'new': 0, 'skipped': 0, 'errors': 0}

    for i, entry in enumerate(articles, 1):
        url = entry['url']
        slug = url.rstrip('/').split('/')[-1]
        print(f"\n[{i}/{len(articles)}] {slug[:70]}")

        if not is_new_url(conn, url):
            print(f"  -> SKIPPED (already in DB)")
            stats['skipped'] += 1
            continue

        detail = fetch_detail(url)
        if not detail['title'] or not detail['content']:
            print(f"  -> ERROR: empty title or content")
            stats['errors'] += 1
            _polite_delay()
            continue

        # Use lastmod as fallback date
        if not detail['date'] and entry['lastmod']:
            detail['date'] = entry['lastmod'][:10]

        print(f"  Title: {detail['title'][:80]}")
        print(f"  Date: {detail['date'] or 'N/A'} | Content: {len(detail['content'])} chars")

        # Generate Chinese title
        title_cn = ''
        try:
            msg = f"Translate this news headline to Chinese (return ONLY the translation, nothing else): {detail['title']}"
            title_cn = llm.chat(msg).strip()[:200]
            print(f"  Title CN: {title_cn[:80]}")
        except Exception:
            pass

        try:
            conn.execute('''
                INSERT OR IGNORE INTO articles
                (source, title, title_cn, content, publish_date, url, summary_cn)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (source_name, detail['title'], title_cn, detail['content'],
                  detail['date'], detail['url'], ''))
            conn.commit()
            stats['new'] += 1
            print(f"  -> INSERTED")
        except Exception as e:
            print(f"  -> DB ERROR: {e}")
            stats['errors'] += 1

        _polite_delay()

    return stats


def main():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    llm = get_llm()

    press_urls, blog_urls = crawl_sitemap()

    # Scrape press releases
    if press_urls:
        print(f"\n{'='*50}")
        print(f"SCRAPING PRESS RELEASES ({len(press_urls)} URLs)")
        print(f"{'='*50}")
        pr_stats = scrape_articles(press_urls, 'D-Wave Press', conn, llm)
    else:
        pr_stats = {'new': 0, 'skipped': 0, 'errors': 0}

    # Scrape blog posts
    if blog_urls:
        print(f"\n{'='*50}")
        print(f"SCRAPING BLOG POSTS ({len(blog_urls)} URLs)")
        print(f"{'='*50}")
        blog_stats = scrape_articles(blog_urls, 'D-Wave Blog', conn, llm)
    else:
        blog_stats = {'new': 0, 'skipped': 0, 'errors': 0}

    conn.close()

    print(f"\n{'='*50}")
    print(f"D-Wave scrape complete:")
    print(f"  Press - New: {pr_stats['new']}  Skipped: {pr_stats['skipped']}  Errors: {pr_stats['errors']}")
    print(f"  Blog  - New: {blog_stats['new']}  Skipped: {blog_stats['skipped']}  Errors: {blog_stats['errors']}")
    print(f"{'='*50}")


if __name__ == '__main__':
    main()
