"""
Auto-detect optimal crawling strategy for a quantum institution website.

Usage:
    python auto_detect.py "IonQ" "https://ionq.com/news"
    python auto_detect.py --json  # outputs machine-readable JSON

The detector probes the URL in priority order and returns the best source config
along with a confidence score (0-1).
"""

import sys, os, re, io, json, time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
TIMEOUT = 15

# Quantum keywords for auto-detecting quantum-native sites
QUANTUM_KEYWORDS = [
    'quantum', 'qubit', 'qiskit', 'cirq', 'quera', 'rigetti',
    'quantinuum', 'ionq', 'xanadu', 'd-wave', 'dwavesys',
    'quantum computing', 'quantum computer', 'quantum processor',
    'superconducting', 'ion trap', 'neutral atom', 'photonic',
    'error correction', 'fault tolerant', 'qkd', 'pqc',
    '量子', '量子计算', '量子比特', '量子计算机',
]


# ---------------------------------------------------------------------------
# Step 1: Sitemap detection
# ---------------------------------------------------------------------------

def detect_sitemap(base_url: str) -> dict:
    """
    Check robots.txt then /sitemap.xml for a sitemap URL.
    Returns {'found': True, 'url': '...', 'pattern': '...'} or {'found': False}.
    """
    parsed = urlparse(base_url)
    domain = f'{parsed.scheme}://{parsed.netloc}'

    # 1a. Try robots.txt
    try:
        robots_url = f'{domain}/robots.txt'
        resp = requests.get(robots_url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code == 200:
            for line in resp.text.split('\n'):
                if line.lower().startswith('sitemap:'):
                    sitemap_url = line.split(':', 1)[1].strip()
                    return {'found': True, 'url': sitemap_url, 'source': 'robots.txt'}
    except Exception:
        pass

    # 1b. Try /sitemap.xml
    try:
        sitemap_url = f'{domain}/sitemap.xml'
        resp = requests.get(sitemap_url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code == 200 and resp.text.strip().startswith('<?xml'):
            return {'found': True, 'url': sitemap_url, 'source': '/sitemap.xml'}
    except Exception:
        pass

    # 1c. Try /sitemap_index.xml
    try:
        sitemap_url = f'{domain}/sitemap_index.xml'
        resp = requests.get(sitemap_url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code == 200 and resp.text.strip().startswith('<?xml'):
            return {'found': True, 'url': sitemap_url, 'source': '/sitemap_index.xml'}
    except Exception:
        pass

    return {'found': False}


def count_sitemap_matches(sitemap_url: str, keyword: str = 'quantum') -> int:
    """Count how many URLs in a sitemap match the keyword."""
    try:
        resp = requests.get(sitemap_url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return 0
        soup = BeautifulSoup(resp.text, 'xml')
        count = 0
        for url in soup.find_all('url'):
            loc = (url.find('loc') or {}).text if url.find('loc') else ''
            if keyword.lower() in loc.lower():
                count += 1
        # If it's a sitemap index, count from sub-sitemaps
        if count == 0:
            for sm in soup.find_all('sitemap'):
                loc = (sm.find('loc') or {}).text if sm.find('loc') else ''
                if 'blog' in loc.lower() or 'post' in loc.lower() or 'news' in loc.lower():
                    sub_count = count_sitemap_matches(loc, keyword)
                    count += sub_count
        return count
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Step 2: Feed detection
# ---------------------------------------------------------------------------

def detect_feed(soup, base_url: str) -> dict:
    """
    Check page for Atom/RSS feed links, then try common paths.
    Returns {'found': True, 'url': '...'} or {'found': False}.
    """
    # 2a. Check <link rel="alternate"> tags
    for link in soup.find_all('link', rel=lambda r: r and 'alternate' in r):
        link_type = link.get('type', '')
        href = link.get('href', '')
        if ('atom' in link_type or 'rss' in link_type or 'xml' in link_type) and href:
            if href.startswith('/'):
                href = urljoin(base_url, href)
            if count_feed_entries(href) > 3:
                return {'found': True, 'url': href, 'source': '<link rel> tag'}

    # 2b. Try common feed paths
    parsed = urlparse(base_url)
    path = parsed.path.rstrip('/')
    feed_candidates = [
        f'{path}/feed/',
        f'{path}/rss/',
        f'{path}/atom.xml',
        f'{parsed.scheme}://{parsed.netloc}/feed/',
        f'{parsed.scheme}://{parsed.netloc}/rss/',
    ]
    for feed_url in feed_candidates:
        count = count_feed_entries(feed_url)
        if count > 3:
            return {'found': True, 'url': feed_url, 'source': 'common path'}

    return {'found': False}


def count_feed_entries(feed_url: str) -> int:
    """Count entries in an Atom/RSS feed."""
    try:
        resp = requests.get(feed_url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            return 0
        soup = BeautifulSoup(resp.text, 'xml')
        return len(soup.find_all(['entry', 'item']))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Step 3: HTML listing page analysis
# ---------------------------------------------------------------------------

def _path_depth(path: str) -> int:
    """Count meaningful path segments."""
    return len([s for s in path.strip('/').split('/') if s])


def _common_prefix(paths: list) -> str:
    """Find the longest common prefix of a list of paths."""
    if not paths:
        return ''
    prefix = paths[0].split('/')
    for p in paths[1:]:
        parts = p.split('/')
        i = 0
        while i < len(prefix) and i < len(parts) and prefix[i] == parts[i]:
            i += 1
        prefix = prefix[:i]
    return '/'.join(prefix) + '/' if prefix else ''


def detect_article_pattern(soup, base_url: str) -> dict:
    """
    Analyze links on the page to find the article URL pattern.
    Prefers blog/news/article-specific paths over root-level patterns.
    Returns {'pattern': '...', 'count': N, 'sample_urls': [...]}.
    """
    parsed_base = urlparse(base_url)
    candidate_paths = []

    for a in soup.find_all('a', href=True):
        title = a.get_text(strip=True)
        href = a['href']
        if len(title) < 20:
            continue
        if href.startswith('/'):
            full = urljoin(base_url, href)
        elif href.startswith('http'):
            full = href
        else:
            continue
        if urlparse(full).netloc != parsed_base.netloc:
            continue
        parsed_full = urlparse(full)
        path = parsed_full.path
        if any(skip in path for skip in ['#', 'login', 'admin', 'wp-admin', 'cdn-cgi']):
            continue
        candidate_paths.append(path)

    if not candidate_paths:
        return {'pattern': '/', 'count': 0, 'total_links': 0, 'coverage': 0}

    # Try to find a blog/news-specific prefix
    blog_news = [p for p in candidate_paths if re.search(r'/(blog|news|article|press|insight|research|technology)/', p, re.I)]
    if len(blog_news) >= len(candidate_paths) * 0.3:
        # Primary: use base_url's path as the pattern
        base_path = urlparse(base_url).path.rstrip('/')
        if base_path and len(base_path) > 1:
            # Also accept links directly under blog/news/research segments
            return {
                'pattern': base_path,
                'count': len(blog_news),
                'total_links': len(candidate_paths),
                'coverage': len(blog_news) / len(candidate_paths),
            }
        prefix = _common_prefix(blog_news)
        if prefix.rstrip('/') == '':
            segments = {}
            for p in blog_news:
                parts = p.strip('/').split('/')
                if parts:
                    seg = '/' + parts[0] + '/'
                    segments[seg] = segments.get(seg, 0) + 1
            prefix = max(segments, key=segments.get) if segments else '/'
        return {
            'pattern': prefix.rstrip('/') or '/',
            'count': len(blog_news),
            'total_links': len(candidate_paths),
            'coverage': len(blog_news) / len(candidate_paths),
        }

    # Fallback: find most specific common prefix with decent coverage
    prefix = _common_prefix(candidate_paths)
    matching = [p for p in candidate_paths if p.startswith(prefix)]
    ratio = len(matching) / len(candidate_paths) if candidate_paths else 0

    if prefix.rstrip('/') == '' and len(candidate_paths) > 5:
        segments = {}
        for p in candidate_paths:
            parts = p.strip('/').split('/')
            if len(parts) >= 2:
                seg = '/' + parts[0] + '/' + parts[1]
                segments[seg] = segments.get(seg, 0) + 1
        if segments:
            best_seg = max(segments, key=segments.get)
            if segments[best_seg] >= len(candidate_paths) * 0.3:
                prefix = best_seg + '/'
                matching = [p for p in candidate_paths if p.startswith(prefix)]
                ratio = len(matching) / len(candidate_paths)

    return {
        'pattern': prefix.rstrip('/') or '/',
        'count': len(matching),
        'total_links': len(candidate_paths),
        'coverage': ratio,
    }


def detect_pagination(soup, base_url: str) -> dict:
    """
    Detect pagination style on the page.
    Returns {'type': 'auto'|'template'|'none', 'template': '...', 'max_pages': N}.
    """
    # Check for <a> pagination with _page=N or ?page=N
    for a in soup.find_all('a', href=True):
        href = a['href']
        text = a.get_text(strip=True).lower()
        if re.search(r'_page=\d+', href) and any(k in text for k in ['view more', 'next', 'older']):
            return {'type': 'auto', 'template': None, 'max_pages': 5, 'source': 'a tag _page=N'}

    # Check for numbered page links
    page_links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        text = a.get_text(strip=True)
        dp = a.get('data-page', '')
        if (dp.isdigit() or text.strip().isdigit()) and re.search(r'[?&]page=\d+', href):
            page_links.append(int(dp or text.strip()))
    if page_links:
        return {'type': 'auto', 'template': None, 'max_pages': max(page_links),
                'source': f'{len(page_links)} numbered page links'}

    # Check for <button> pagination (IBM-style)
    for button in soup.find_all(['button', 'a'], attrs={'data-page': True}):
        dp = int(button.get('data-page', 1))
        if dp > 1:
            # Try to detect the URL pattern by testing ?page=2
            test_url = base_url + ('&' if '?' in base_url else '?') + 'page=2'
            try:
                resp = requests.get(test_url, headers=HEADERS, timeout=TIMEOUT)
                if resp.status_code == 200:
                    template = ('&page={n}' if '?' in base_url else '?page={n}')
                    return {'type': 'template', 'template': template, 'max_pages': 5,
                            'source': f'button data-page={dp}'}
            except Exception:
                pass
            # Try /page/2/ pattern
            test_url2 = base_url.rstrip('/') + '/page/2/'
            try:
                resp2 = requests.get(test_url2, headers=HEADERS, timeout=TIMEOUT)
                if resp2.status_code == 200:
                    return {'type': 'template', 'template': 'page/{n}/', 'max_pages': 5,
                            'source': f'button data-page={dp} -> /page/N/'}
            except Exception:
                pass

    return {'type': 'none', 'template': None, 'max_pages': 1}


def _extract_date_from_text(text: str) -> str:
    """Quick date extraction for detection purposes."""
    m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if m:
        return m.group(1)
    m = re.search(r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})', text)
    if m:
        try:
            return datetime.strptime(m.group(1), '%d %b %Y').strftime('%Y-%m-%d')
        except ValueError:
            pass
    return ''


def detect_date_coverage(articles: list) -> float:
    """What fraction of articles have extractable dates?"""
    if not articles:
        return 0.0
    dated = sum(1 for a in articles if a.get('date'))
    return dated / len(articles)


def detect_quantum_native(articles: list) -> bool:
    """Check if most article titles contain quantum keywords."""
    if not articles:
        return False
    hits = 0
    for a in articles:
        title = a.get('title', '').lower()
        if any(kw in title for kw in QUANTUM_KEYWORDS):
            hits += 1
    return (hits / len(articles)) > 0.4  # 40%+ titles mention quantum


# ---------------------------------------------------------------------------
# Main detector
# ---------------------------------------------------------------------------

def _sitemap_article_ratio(sitemap_url: str) -> float:
    """Check what fraction of sitemap URLs look like articles.
    Handles both regular sitemaps and sitemap indexes."""
    try:
        resp = requests.get(sitemap_url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return 0.0
        soup = BeautifulSoup(resp.text, 'xml')

        # Check for sitemap index
        sub_sitemaps = soup.find_all('sitemap')
        if sub_sitemaps:
            article_sm = []
            for sm in sub_sitemaps:
                loc = (sm.find('loc') or {}).text if sm.find('loc') else ''
                if re.search(r'(blog|post|news|article)', loc, re.I):
                    article_sm.append(loc)
            if not article_sm:
                return 0.0
            resp2 = requests.get(article_sm[0], headers=HEADERS, timeout=30)
            if resp2.status_code != 200:
                return 0.0
            soup = BeautifulSoup(resp2.text, 'xml')

        urls = [u.find('loc').text for u in soup.find_all('url') if u.find('loc')]
        if not urls:
            return 0.0
        article = sum(1 for u in urls if re.search(r'/(blog|news|article|press|research|technology|insight)/', u, re.I))
        return article / len(urls)
    except Exception:
        return 0.0


def detect_source(name: str, url: str) -> dict:
    """
    Auto-detect the best crawling strategy for a given URL.

    Priority: Feed > Sitemap (with article-filter) > HTML listing

    Returns a source configuration dict plus detection metadata.
    """
    result = {
        'name': name,
        'url': url,
        'detection': {},
        'config': None,
        'confidence': 0.0,
    }

    parsed = urlparse(url)
    domain = f'{parsed.scheme}://{parsed.netloc}'

    # Fetch the page first (needed for feed + HTML detection)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            result['detection']['error'] = f'HTTP {resp.status_code}'
            return result
        soup = BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        result['detection']['error'] = str(e)
        return result

    # ---- Priority 1: Atom/RSS Feed (best data quality) ----
    feed = detect_feed(soup, url)
    if feed['found']:
        entry_count = count_feed_entries(feed['url'])
        if entry_count >= 10:  # Feed must have substantial content
            try:
                f_resp = requests.get(feed['url'], headers=HEADERS, timeout=TIMEOUT)
                f_soup = BeautifulSoup(f_resp.text, 'xml')
                titles = [e.find('title').text for e in f_soup.find_all(['entry', 'item']) if e.find('title')]
                q_hits = sum(1 for t in titles if any(kw in t.lower() for kw in QUANTUM_KEYWORDS))
                q_ratio = q_hits / len(titles) if titles else 0
            except Exception:
                titles, q_hits, q_ratio = [], 0, 0

            # Require at least some quantum relevance in feed
            if q_ratio > 0.15 or entry_count > 30:
                result['detection']['feed'] = {
                    'url': feed['url'], 'entries': entry_count,
                    'quantum_titles': q_hits, 'quantum_ratio': q_ratio,
                }
                result['config'] = {
                    'name': name, 'type': 'atom', 'url': feed['url'],
                    'url_pattern': '/blog/' if '/blog/' in feed['url'] else '/',
                    'quantum_native': q_ratio > 0.3 if titles else True,
                }
                result['confidence'] = 0.90 if entry_count > 30 else 0.75
                return result

    # ---- Priority 2: Sitemap (only if URLs look like articles) ----
    sm = detect_sitemap(url)
    if sm['found']:
        quantum_matches = count_sitemap_matches(sm['url'], 'quantum')
        article_ratio = _sitemap_article_ratio(sm['url'])
        # Accept sitemap if:
        #  - 20+ quantum matches (plenty of content, even in large sitemap)
        #  - article_ratio > 0.3 (most URLs are articles)
        #  - 5-100 quantum matches with article_ratio > 0.05 (reasonable signal)
        # Accept sitemap if:
        #  - 20-200 quantum matches (targeted, not site-wide noise)
        #  - article_ratio > 0.3 (most URLs are articles)
        #  - 5-100 matches with article_ratio > 0.05 (reasonable signal)
        # Reject if >200 matches (entire company site, too noisy) unless article_ratio > 0.3
        sitemap_ok = (20 <= quantum_matches <= 200) or (article_ratio > 0.3) or \
                     (5 <= quantum_matches <= 100 and article_ratio > 0.05)
        if sitemap_ok:
            result['detection']['sitemap'] = {
                'url': sm['url'], 'quantum_urls': quantum_matches,
                'article_ratio': article_ratio,
            }
            keyword = 'quantum' if quantum_matches > 5 else '/'
            result['config'] = {
                'name': name, 'type': 'sitemap', 'url': sm['url'],
                'url_pattern': keyword,
                'quantum_native': quantum_matches > 5,
            }
            result['confidence'] = 0.95 if 10 <= quantum_matches <= 100 else 0.70
            return result

    # ---- Priority 3: HTML listing page analysis ----

    # --- HTML listing page analysis ---
    # Extract articles
    article_info = detect_article_pattern(soup, url)
    articles_raw = []
    for a in soup.find_all('a', href=True):
        title = a.get_text(strip=True)
        href = a['href']
        if len(title) < 20:
            continue
        if href.startswith('/'):
            full = urljoin(url, href)
        elif href.startswith('http'):
            full = href
        else:
            continue
        if urlparse(full).netloc != parsed.netloc:
            continue
        if not full.startswith(domain + article_info['pattern']):
            continue
        # Try to extract date from parent
        d = ''
        parent = a.parent
        for _ in range(3):
            if not parent:
                break
            text = parent.get_text(strip=True)
            d = _extract_date_from_text(text)
            if d:
                break
            parent = parent.parent
        articles_raw.append({'title': title, 'url': full, 'date': d})

    pagination = detect_pagination(soup, url)
    date_cov = detect_date_coverage(articles_raw[:15])
    quantum_native = detect_quantum_native(articles_raw[:20])

    result['detection']['html'] = {
        'article_pattern': article_info,
        'articles_found': len(articles_raw),
        'pagination': pagination,
        'date_coverage': date_cov,
        'quantum_native': quantum_native,
    }

    # Build config
    config = {
        'name': name,
        'type': 'enterprise',
        'url': url,
        'article_selector': 'a',
        'url_pattern': article_info['pattern'],
        'quantum_native': quantum_native,
    }

    if pagination['type'] == 'auto':
        config['max_pages'] = pagination['max_pages']
        confidence = 0.85 if date_cov > 0.3 else 0.70
    elif pagination['type'] == 'template':
        config['max_pages'] = pagination['max_pages']
        config['page_url_template'] = pagination['template']
        confidence = 0.70 if date_cov > 0.3 else 0.60
    else:
        confidence = 0.65 if date_cov > 0.3 else 0.50

    result['config'] = config
    result['confidence'] = confidence
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_result(result: dict):
    """Pretty-print detection result."""
    print(f"\n{'='*60}")
    print(f"  Auto-Detect: {result['name']}")
    print(f"  URL: {result['url']}")
    print(f"  Confidence: {result['confidence']:.0%}")
    print(f"{'='*60}")

    det = result.get('detection', {})
    if 'error' in det:
        print(f"  ERROR: {det['error']}")
        return

    if 'sitemap' in det:
        print(f"  Mode: SITEMAP")
        print(f"  Sitemap URL: {det['sitemap']['url']}")
        print(f"  Quantum URLs: {det['sitemap']['quantum_urls']}")
    elif 'feed' in det:
        print(f"  Mode: ATOM/RSS FEED")
        print(f"  Feed URL: {det['feed']['url']}")
        print(f"  Entries: {det['feed']['entries']}")
        print(f"  Quantum titles: {det['feed'].get('quantum_titles', '?')}")
    elif 'html' in det:
        html = det['html']
        print(f"  Mode: HTML LISTING")
        print(f"  Article pattern: {html['article_pattern']['pattern']}")
        print(f"  Articles found: {html['articles_found']}")
        print(f"  Pagination: {html['pagination']['type']} ({html['pagination']['source']})")
        print(f"  Date coverage: {html['date_coverage']:.0%}")
        print(f"  Quantum-native: {html['quantum_native']}")

    if result['config']:
        print(f"\n  --- Recommended Config ---")
        print(f"  {json.dumps(result['config'], indent=2, ensure_ascii=False)}")


SOURCE_TEMPLATE = '''"""Crawl {name} news."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.base import BaseCrawler
from core.llm import get_llm

SOURCE = {{
    "name": "{name}",
    "type": "{crawl_type}",
    "url": "{url}",
    "url_pattern": "{url_pattern}",
    "quantum_native": {quantum_native},{extra}
}}

if __name__ == '__main__':
    crawler = BaseCrawler(SOURCE)
    crawler.connect_db()
    crawler.set_llm(get_llm())
    new_count = crawler.run()
    crawler.conn.close()
    print(f'[OK] {{new_count}} new articles from {name}')
'''


def generate_source_file(result: dict, output_dir: str = None) -> str:
    """Write a source file from detection result. Returns the file path."""
    cfg = result['config']
    if not cfg:
        raise ValueError("No valid config to generate")

    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sources')

    # Derive filename from name (just lowercase + underscores)
    filename = cfg['name'].lower().replace(' ', '_').replace('-', '_') + '.py'

    # Build extra config lines
    extra_lines = []
    max_pages = result.get('detection', {}).get('html', {}).get('pagination', {}).get('max_pages', 1)
    if max_pages > 1:
        extra_lines.append(f'"max_pages": {max_pages}')
    page_template = cfg.get('page_url_template', '')
    if page_template:
        extra_lines.append(f'"page_url_template": "{page_template}"')

    extra_str = ''
    if extra_lines:
        extra_str = '\n    ' + ',\n    '.join(extra_lines) + ','

    content = SOURCE_TEMPLATE.format(
        name=cfg['name'],
        crawl_type=cfg.get('type', 'enterprise'),
        url=cfg['url'],
        url_pattern=cfg.get('url_pattern', '/'),
        quantum_native=str(cfg.get('quantum_native', True)),
        extra=extra_str,
    )

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return filepath


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        print("Usage: python auto_detect.py <name> <url>")
        print("       python auto_detect.py <name> <url> --generate")
        print("       python auto_detect.py --json <name> <url>")
        print("       python auto_detect.py --test")
        sys.exit(0)

    if sys.argv[1] == '--json':
        name, url = sys.argv[2], sys.argv[3]
        result = detect_source(name, url)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    elif sys.argv[1] == '--test':
        _run_tests()
    else:
        name, url = sys.argv[1], sys.argv[2]
        generate = '--generate' in sys.argv
        result = detect_source(name, url)
        _print_result(result)
        if generate and result['config']:
            path = generate_source_file(result)
            print(f'\n  -> Source file written: {path}')


def _run_tests():
    """Test auto-detection against the 5 known institutions."""
    test_cases = [
        ('IBM Quantum', 'https://www.ibm.com/quantum/blog'),
        ('Quantinuum', 'https://www.quantinuum.com/news/blog'),
        ('Google Quantum AI', 'https://blog.google/technology/research/'),
        ('Microsoft Azure Quantum', 'https://cloudblogs.microsoft.com/quantum/'),
        ('NVIDIA Quantum', 'https://developer.nvidia.com/blog/tag/quantum-computing/'),
    ]

    results = {}
    for name, url in test_cases:
        print(f'\nProbing {name}...', end=' ', flush=True)
        result = detect_source(name, url)
        results[name] = result
        actual_type = result['config']['type'] if result['config'] else 'ERROR'
        conf = result.get('confidence', 0)
        print(f'-> {actual_type} (confidence: {conf:.0%})')
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    for name, r in results.items():
        cfg = r['config'] or {}
        print(f"  {name:25s} | {cfg.get('type','?'):10s} | {r['confidence']:.0%} | {r.get('detection',{})}")


if __name__ == '__main__':
    main()
