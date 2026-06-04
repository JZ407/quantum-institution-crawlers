"""D-Wave press release crawler - sitemap-based.

Sources:
  - /company/newsroom/press-release/ — 246 press releases (sitemap)
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
SOURCE = 'D-Wave Press'

# Date formats on detail pages: "June 01, 2026"
DATE_RE = re.compile(
    r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}'
)
MONTH_MAP = {
    'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5, 'June': 6,
    'July': 7, 'August': 8, 'September': 9, 'October': 10, 'November': 11, 'December': 12
}


def _polite_delay(min_s=1.0, max_s=3.0):
    time.sleep(random.uniform(min_s, max_s))


def crawl_sitemap():
    """Parse sitemap XML, extract all press release URLs with lastmod dates."""
    print(f"Fetching sitemap: {SITEMAP_URL}")
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        print(f"  ERROR: HTTP {resp.status_code}")
        return []

    # Parse all <url> blocks
    urls = []
    for block in re.finditer(r'<url>(.*?)</url>', resp.text, re.DOTALL):
        loc = re.search(r'<loc>([^<]+)</loc>', block.group(1))
        lastmod = re.search(r'<lastmod>([^<]+)</lastmod>', block.group(1))
        # > 5 slashes = actual article (e.g. /company/newsroom/press-release/slug/)
        # <= 5 slashes = listing page (e.g. /company/newsroom/press-release/)
        if loc and '/press-release/' in loc.group(1) and loc.group(1).rstrip('/').count('/') >= 6:
            urls.append({
                'url': loc.group(1),
                'lastmod': lastmod.group(1) if lastmod else None
            })

    print(f"  Found {len(urls)} press release URLs in sitemap")
    return urls


def fetch_detail(url):
    """Extract title, date, content from a D-Wave press release page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return {'title': '', 'date': '', 'content': '', 'url': url}
        soup = BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        print(f"    HTTP error: {e}")
        return {'title': '', 'date': '', 'content': '', 'url': url}

    # Title: h1 (og:title is empty on this site)
    title = ''
    h1 = soup.find('h1')
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        ttag = soup.find('title')
        if ttag:
            title = ttag.get_text(strip=True).split('|')[0].strip()

    # Date: in div with class containing 't-news-section-text-item'
    # Format: "June 01, 2026D-Wave Charts..."
    date_str = ''
    date_div = soup.find('div', class_=lambda c: c and 't-news-section-text-item' in ' '.join(c) if isinstance(c, list) else False)
    if date_div:
        text = date_div.get_text(strip=True)
        m = DATE_RE.search(text)
        if m:
            date_str = m.group(0)
    # Fallback: search full body text for date pattern
    if not date_str:
        body = soup.find('body')
        if body:
            m = DATE_RE.search(body.get_text())
            if m:
                date_str = m.group(0)

    # Parse date to YYYY-MM-DD
    publish_date = ''
    if date_str:
        try:
            parts = date_str.split(' ')
            month = MONTH_MAP.get(parts[0], 1)
            day = int(parts[1].rstrip(','))
            year = int(parts[2])
            publish_date = f'{year}-{month:02d}-{day:02d}'
        except (ValueError, IndexError):
            pass

    # Content: body minus navigation/footer noise
    content = ''
    body = soup.find('body')
    if body:
        for noise in body.find_all(['nav', 'header', 'footer', 'script', 'style']):
            noise.decompose()
        lines = [l.strip() for l in body.get_text(separator='\n').split('\n') if l.strip()]
        # Find content start: after the date/title section
        start = 0
        for i, l in enumerate(lines):
            if date_str and date_str[:10] in l:
                start = i + 1
                break
        if start == 0:
            # Fallback: skip short lines (nav crumbs, labels) until first long paragraph
            for i, l in enumerate(lines):
                if len(l) > 80:
                    start = i
                    break
        # Collect content lines
        result = []
        for l in lines[start:]:
            # Stop at footer noise
            if any(kw in l for kw in ['Investor Contact', 'Media Contact', 'About D-Wave',
                                        'Forward-Looking Statements', 'D-Wave Quantum Inc.',
                                        'Privacy Policy', 'Terms of Use', 'Cookie Policy']):
                break
            result.append(l)
        content = '\n'.join(result).strip()

    return {
        'title': title,
        'date': publish_date,
        'content': content,
        'url': url
    }


def main():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    llm = get_llm()

    press_urls = crawl_sitemap()
    if not press_urls:
        print("No press releases found.")
        conn.close()
        return

    stats = {'new': 0, 'skipped': 0, 'errors': 0}

    for i, entry in enumerate(press_urls, 1):
        url = entry['url']
        slug = url.rstrip('/').split('/')[-1]
        print(f"\n[{i}/{len(press_urls)}] {slug[:70]}")

        # Check if already in DB
        if not is_new_url(conn, url):
            print(f"  -> SKIPPED (already in DB)")
            stats['skipped'] += 1
            continue

        # Fetch detail
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

        # Insert into DB
        try:
            conn.execute('''
                INSERT OR IGNORE INTO articles
                (source, title, title_cn, content, publish_date, url, summary_cn)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (SOURCE, detail['title'], title_cn, detail['content'],
                  detail['date'], detail['url'], ''))
            conn.commit()
            stats['new'] += 1
            print(f"  -> INSERTED")
        except Exception as e:
            print(f"  -> DB ERROR: {e}")
            stats['errors'] += 1

        _polite_delay()

    conn.close()
    print(f"\n{'='*50}")
    print(f"D-Wave scrape complete:")
    print(f"  New: {stats['new']}")
    print(f"  Skipped: {stats['skipped']}")
    print(f"  Errors: {stats['errors']}")
    print(f"{'='*50}")


if __name__ == '__main__':
    main()
