"""Content extraction utilities: date, title, body, article-URL filter."""
import re, json
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse


def extract_date(soup, text: str) -> str:
    """Extract publish date from meta, time, JSON-LD, or regex."""
    if soup is None:
        return _extract_date_regex(text)
    for meta in soup.find_all('meta'):
        prop = (meta.get('property', '') or meta.get('name', '')).lower()
        if any(k in prop for k in ['date', 'published', 'modified', 'article:published']):
            d = meta.get('content', '')[:10]
            if re.match(r'\d{4}-\d{2}-\d{2}', d):
                return d

    # 2. JSON-LD (more reliable than <time> which may be modified date)
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string)
            d = ''
            if isinstance(data, dict):
                d = data.get('datePublished') or data.get('dateModified') or ''
            elif isinstance(data, list) and data:
                d = data[0].get('datePublished', '')
            if d and re.match(r'\d{4}-\d{2}-\d{2}', str(d)[:10]):
                return str(d)[:10]
        except Exception:
            pass

    # 3. Text regex (often has publish date: \"16 February 2026\")
    regex_date = _extract_date_regex(text)
    if regex_date:
        return regex_date

    # 4. <time> element (last resort — often modified date, not publish date)
    t = soup.find('time')
    if t:
        dt = t.get('datetime', '') or t.get_text(strip=True)
        if dt:
            return dt[:10] if re.match(r'\d{4}-\d{2}-\d{2}', dt[:10]) else dt

    return ''


def _extract_date_regex(text: str) -> str:
    """Fallback: extract date from text using regex."""
    head_text = text[:2000] if text else ''
    m = re.search(r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})', head_text)
    if m:
        try:
            return datetime.strptime(m.group(1), '%d %b %Y').strftime('%Y-%m-%d')
        except ValueError:
            pass
    m = re.search(r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})', head_text)
    if m:
        try:
            return datetime.strptime(m.group(1).replace(',', ''), '%B %d %Y').strftime('%Y-%m-%d')
        except ValueError:
            pass
    m = re.search(r'(\d{4}-\d{2}-\d{2})', head_text)
    if m:
        return m.group(1)
    return ''


def extract_page_title(soup) -> str:
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


def extract_body(soup) -> str:
    """Extract clean article body from common content containers."""
    for selector in ['article', '[role=main]', 'main',
                     '[class*=article-body]', '[class*=post-body]',
                     '[class*=entry-content]', '[class*=blog-content]',
                     '[class*=article-content]', '[class*=post-content]']:
        el = soup.select_one(selector)
        if el:
            for tag in el.find_all(['script', 'style', 'nav', 'footer', 'aside',
                                     'header', '.sidebar', '.related-posts',
                                     '.comments', '.social-share']):
                tag.decompose()
            text = el.get_text(separator='\n', strip=True)
            if len(text) > 300:
                return text
    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'header',
                               'aside', '.sidebar', '.menu', '.navigation',
                               '.related', '.comments', '.footer']):
        tag.decompose()
    text = soup.get_text(separator='\n', strip=True)
    lines = [l.strip() for l in text.split('\n') if len(l.strip()) > 30]
    return '\n'.join(lines)


def parse_atom_date(date_str: str) -> str:
    """Parse Atom/RSS date formats to YYYY-MM-DD."""
    if not date_str:
        return ''
    m = re.match(r'(\d{4}-\d{2}-\d{2})', date_str)
    if m:
        return m.group(1)
    try:
        return parsedate_to_datetime(date_str).strftime('%Y-%m-%d')
    except Exception:
        pass
    for fmt in ['%d %b %Y', '%B %d, %Y']:
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return ''


def is_article_url(url: str) -> bool:
    """Check if a URL looks like an article (not a product/nav page)."""
    path = urlparse(url).path.lower()
    article_patterns = ['/blog/', '/news/', '/press/', '/insight/', '/article/',
                        '/post/', '/event/', '/story/', '/learn/', '/resource/',
                        '/research/', '/news-import/',
                        '/innovation-and-ai/', '/technology/', '/security/']
    for p in article_patterns:
        if p in path:
            if not path.rstrip('/').endswith(('/category', '/tag', '/author', '/page')):
                return True
    endings = ['/blog', '/news', '/press', '/insight', '/research']
    for e in endings:
        if path.rstrip('/').endswith(e):
            return True
    return False
