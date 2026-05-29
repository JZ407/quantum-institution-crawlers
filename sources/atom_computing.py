"""Atom Computing news + publications crawler - standalone, HTML listing."""
import sys, os, re, time, requests, sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm import get_llm
from core.db import DB_PATH, init_db, is_new_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'en-US,en;q=0.9',
}
LISTING_URL = 'https://atom-computing.com/news-resources/'
SCI_URL = 'https://atom-computing.com/news-resources/scientific-publications/'
DATE_RE = re.compile(r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})')

# Static pages to skip
SKIP_PATHS = [
    'quantum-computing-technology', 'white-paper', 'news-resources',
    'tech-perspectives', 'sales-inquiry', 'partner-inquiry',
    'media-analyst-inquiry', 'investor-inquiry', 'contact-us',
    'careers', 'about-us', 'cookie-policy', 'privacy-policy',
    'rsvp-submission', 'form-submission', 'ac1000', 'quantum-denmark',
    'category',
]
JOURNAL_DOMAINS = [
    'arxiv.org', 'journals.aps.org', 'link.aps.org', 'nature.com',
    'pubs.acs.org', 'iopscience.iop.org', 'link.springer.com',
    'pnas.org', 'science.org', 'quantum-journal.org',
]


def crawl_listing():
    """Extract press releases and scientific publications from listing pages."""
    s = requests.Session()
    press_releases = {}
    publications = []

    for url, page_type in [(LISTING_URL, 'press'), (SCI_URL, 'publication')]:
        resp = s.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')

        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)

            if page_type == 'press' and 'atom-computing.com' in href and len(text) >= 15:
                path = href.replace('https://atom-computing.com/', '').rstrip('/')
                if any(path.startswith(s) for s in SKIP_PATHS):
                    continue
                if href not in press_releases:
                    # Find date from parent card
                    card = _find_card(a)
                    card_text = card.get_text() if card else ''
                    m = DATE_RE.search(card_text)
                    d = _parse_date(m.group(1)) if m else ''
                    press_releases[href] = {'title': _clean_title(text), 'url': href, 'date': d}

            elif page_type == 'publication':
                domain = href.split('/')[2] if '://' in href else ''
                is_journal = any(jd in domain for jd in JOURNAL_DOMAINS)
                if is_journal and len(text) < 5:
                    # Find the card div (class="card") that contains this publication
                    card = a
                    for _ in range(8):
                        if not card:
                            break
                        classes = card.get('class', [])
                        if 'card' in classes:
                            break
                        card = card.parent
                    if not card or 'card' not in (card.get('class') or []):
                        continue

                    card_text = card.get_text(separator='\n', strip=True)
                    lines = [l.strip() for l in card_text.split('\n') if l.strip()]

                    # Date: first date pattern in card
                    d = ''
                    m = DATE_RE.search(card_text)
                    if m:
                        d = _parse_date(m.group(1))

                    # Title: longest non-label line
                    skip_words = {'Go to Publication', 'Scientific Publications', 'Media Resources'}
                    title_candidates = [l for l in lines if l not in skip_words and len(l) > 20]
                    title = re.sub(r'^\w{3,9}\s+\d{1,2},?\s+\d{4}\s+', '', title_candidates[0]) if title_candidates else ''
                    title = _clean_title(title)

                    # Journal: short line that's not the title or date
                    journal = ''
                    for l in lines:
                        if l != title and l not in skip_words and 5 < len(l) < 60:
                            if not DATE_RE.match(l):
                                journal = l
                                break

                    if title:
                        publications.append({
                            'title': title, 'url': href, 'date': d,
                            'journal': journal,
                        })

    # Deduplicate by URL
    unique_pubs = []
    seen_pub = set()
    for p in publications:
        if p['url'] not in seen_pub:
            seen_pub.add(p['url'])
            unique_pubs.append(p)

    return list(press_releases.values()), unique_pubs


def _find_card(a):
    """Find the parent card/article element."""
    el = a.parent
    for _ in range(8):
        if not el:
            return None
        if el.name in ('article', 'section'):
            return el
        el = el.parent
    return a.parent.parent if a.parent else None


def _parse_date(date_str):
    try:
        return datetime.strptime(date_str, '%B %d, %Y').strftime('%Y-%m-%d')
    except ValueError:
        return ''


def _clean_title(text):
    return re.sub(r'\s+', ' ', text.strip())


def fetch_detail(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {'content': '', 'date': '', 'title': ''}
        soup = BeautifulSoup(resp.text, 'html.parser')

        title = ''
        h1 = soup.find('h1')
        if h1:
            title = _clean_title(h1.get_text())
        if not title:
            og = soup.find('meta', property='og:title')
            if og:
                title = og.get('content', '').split(' - Atom Computing')[0].strip()

        d = ''
        m = DATE_RE.search(soup.get_text())
        if m:
            d = _parse_date(m.group(1))

        content_div = soup.find(class_=re.compile(r'content'))
        content = content_div.get_text(separator='\n', strip=True) if content_div else soup.get_text(separator='\n', strip=True)

        return {'content': content, 'date': d, 'title': title}
    except Exception:
        return {'content': '', 'date': '', 'title': ''}


if __name__ == '__main__':
    print(f'[CRAWL] Atom Computing: {LISTING_URL}')

    press_releases, publications = crawl_listing()
    print(f'  Press Releases: {len(press_releases)}')
    print(f'  Scientific Publications: {len(publications)}')

    conn = init_db()
    client = get_llm()
    total_new = 0

    # Press releases - full detail fetch
    print(f'\n--- Atom Computing News ({len(press_releases)} URLs) ---')
    for art in press_releases:
        if not is_new_url(conn, art['url']):
            continue

        detail = fetch_detail(art['url'])
        content = detail['content']
        pub_date = detail['date'] or art['date']
        title = detail['title'] or art['title']

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
                (title, content, art['url'], 'Atom Computing', pub_date, summary, title_cn),
            )
            conn.commit()
            total_new += 1
            print(f'  [{pub_date}] {title[:80]}')
        except sqlite3.IntegrityError:
            pass
        time.sleep(0.15)

    # Scientific publications - title + link only (no detail fetch)
    print(f'\n--- Atom Computing Publications ({len(publications)} URLs) ---')
    for pub in publications:
        if not is_new_url(conn, pub['url']):
            continue

        content = pub.get('journal', '')
        pub_date = pub['date']
        title = pub['title']

        try:
            c = conn.cursor()
            c.execute(
                '''INSERT INTO articles (title, content, url, source, publish_date, summary, title_cn)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (title, content, pub['url'], 'Atom Computing Publications', pub_date, '', ''),
            )
            conn.commit()
            total_new += 1
            print(f'  [{pub_date}] {title[:80]}')
        except sqlite3.IntegrityError:
            pass

    conn.close()
    print(f'\n[OK] {total_new} total new articles')
