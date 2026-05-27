"""Base crawler with shared logic for atom, sitemap, and HTML listing modes."""
import re, time, requests
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

from . import db as db_module
from .extractors import (extract_date, extract_page_title, extract_body,
                          parse_atom_date, is_article_url)

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


class BaseCrawler:
    def __init__(self, source: dict):
        self.source = source
        self.name = source['name']
        self.url = source['url']
        self.url_pattern = source.get('url_pattern', '/')
        self.quantum_native = source.get('quantum_native', True)
        self.max_pages = source.get('max_pages', 1)
        self.page_template = source.get('page_url_template', '')
        self.crawl_type = source.get('type', 'enterprise')
        self.conn = None
        self.client = None
        self.db_path = db_module.DB_PATH

    def connect_db(self):
        self.conn = db_module.init_db()

    def set_llm(self, client):
        self.client = client

    def run(self) -> int:
        """Execute crawl and return number of new articles."""
        if not self.conn:
            self.connect_db()

        print(f'\n[CRAWL] {self.name}: {self.url}')

        if self.crawl_type == 'sitemap':
            articles = self._crawl_sitemap()
        elif self.crawl_type == 'atom':
            articles = self._crawl_atom()
        else:
            articles = self._crawl_listing()

        print(f'  Found {len(articles)} articles')
        print(f'  Quantum-native (all {len(articles)} kept)')

        new_count = 0
        for art in articles:
            if not db_module.is_new_url(self.conn, art['url']):
                continue

            detail = self._fetch_detail(art['url'])
            content = detail['content']
            pub_date = detail['date'] or art['date']
            best_title = art['title']
            if detail.get('title') and len(detail['title']) > len(best_title):
                best_title = detail['title']

            summary = content[:300].strip() if content else ''
            summary_cn = ''
            if content and self.client:
                try:
                    cn_msg = [
                        {"role": "system", "content": "你是量子科技翻译专家。请将以下英文文章内容总结为一句话中文摘要（100字以内）。只输出中文，不要解释。"},
                        {"role": "user", "content": f"标题：{best_title}\n\n内容：{content[:2000]}"},
                    ]
                    summary_cn = self.client.chat(cn_msg).strip()
                    if len(summary_cn) > 200:
                        summary_cn = summary_cn[:200]
                except Exception:
                    pass

            try:
                db_module.insert_article(self.conn, best_title, content, art['url'],
                                         self.name, pub_date, summary, summary_cn)
                new_count += 1
            except Exception:
                pass

        self.conn.commit()
        return new_count

    # ---- Internal: fetch detail page ----
    def _fetch_detail(self, url: str) -> dict:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                return {'content': '', 'date': '', 'title': ''}
            soup = BeautifulSoup(resp.text, 'html.parser')
            body = extract_body(soup)
            full_text = soup.get_text(separator='\n', strip=True)
            d = extract_date(soup, full_text)
            t = extract_page_title(soup)
            content = body if body else full_text
            return {'content': content, 'date': d, 'title': t}
        except Exception:
            return {'content': '', 'date': '', 'title': ''}

    # ---- Internal: Atom feed ----
    def _crawl_atom(self) -> list:
        try:
            resp = requests.get(self.url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                print(f'  HTTP {resp.status_code}')
                return []
            soup = BeautifulSoup(resp.text, 'xml')
            articles = []
            for entry in soup.find_all(['entry', 'item']):
                title = (entry.find('title') or {}).text if entry.find('title') else ''
                link_el = entry.find('link')
                link = ''
                if link_el:
                    link = link_el.get('href', '') or link_el.text or ''
                pub_el = entry.find('published') or entry.find('pubDate')
                pub = parse_atom_date(pub_el.text) if pub_el else ''
                if link and self.url_pattern in link.lower():
                    articles.append({'title': title.strip(), 'url': link.strip(), 'date': pub})
            return articles
        except Exception as e:
            print(f'  Error: {e}')
            return []

    # ---- Internal: Sitemap ----
    def _crawl_sitemap(self) -> list:
        try:
            resp = requests.get(self.url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                print(f'  HTTP {resp.status_code}')
                return []
            soup = BeautifulSoup(resp.text, 'xml')
            keyword = self.url_pattern.lower()

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
            for url_el in soup.find_all('url'):
                loc = (url_el.find('loc') or {}).text if url_el.find('loc') else ''
                lastmod = (url_el.find('lastmod') or {}).text if url_el.find('lastmod') else ''
                if keyword in loc.lower() and is_article_url(loc):
                    slug = loc.rstrip('/').rsplit('/', 1)[-1].replace('-', ' ')
                    title = ' '.join(w[0].upper() + w[1:] if w else w for w in slug.split())
                    articles.append({'title': title, 'url': loc, 'date': lastmod[:10] if lastmod else ''})
            return articles
        except Exception as e:
            print(f'  Error: {e}')
            return []

    # ---- Internal: HTML listing ----
    def _crawl_listing(self) -> list:
        try:
            resp = requests.get(self.url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                print(f'  HTTP {resp.status_code}')
                return []
            soup = BeautifulSoup(resp.text, 'html.parser')
            articles = self._extract_articles_from_soup(soup, self.url)

            seen_urls = {a['url'] for a in articles}
            page_num = 1
            while page_num < self.max_pages:
                next_url = self._find_next_page(soup, self.url, page_num)
                if not next_url:
                    break
                page_num += 1
                time.sleep(0.5)
                resp = requests.get(next_url, headers=HEADERS, timeout=20)
                if resp.status_code != 200:
                    break
                soup = BeautifulSoup(resp.text, 'html.parser')
                new_articles = self._extract_articles_from_soup(soup, next_url)
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

    def _extract_articles_from_soup(self, soup, base_url: str) -> list:
        articles = []
        parsed_base = urlparse(base_url)
        for a in soup.find_all('a', href=True):
            title = a.get_text(strip=True)
            href = a['href']
            if len(title) < 15:
                continue
            if href.startswith('/'):
                href = urljoin(base_url, href)
            elif not href.startswith('http'):
                continue
            if urlparse(href).netloc != parsed_base.netloc:
                continue
            if self.url_pattern not in href:
                continue
            d = ''
            parent = a.parent
            for _ in range(3):
                if not parent:
                    break
                for el in parent.find_all(['time', 'span', 'div', 'p']):
                    text = el.get_text(strip=True)
                    d = extract_date(None, text)  # None soup: skip meta check, use regex only
                    if d:
                        break
                if d:
                    break
                parent = parent.parent
            articles.append({'title': title, 'url': href, 'date': d})
        return articles

    def _find_next_page(self, soup, base_url: str, current_page: int) -> str:
        next_page = current_page + 1
        if self.page_template:
            return base_url + self.page_template.format(n=next_page)

        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True).lower()
            if re.search(rf'_page={next_page}\b', href):
                if any(k in text for k in ['view more', 'next', 'older']) or text.strip() == str(next_page):
                    if href.startswith('?'):
                        return base_url + href
                    if href.startswith('/'):
                        return urljoin(base_url, href)
                    if href.startswith('http'):
                        return href
                    return urljoin(base_url, href)
            if re.search(rf'[?&]page={next_page}\b', href) or a.get('data-page') == str(next_page):
                if text.strip() == str(next_page) or 'page' in text:
                    if href.startswith('?') or href.startswith('/'):
                        return urljoin(base_url, href)
                    if href.startswith('http'):
                        return href
                    return urljoin(base_url, href)

        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True).lower()
            href = a['href']
            if any(k in text for k in ['next', 'older posts', 'older entries']):
                if re.search(r'[?&]page=\d+', href):
                    if href.startswith('?') or href.startswith('/'):
                        return urljoin(base_url, href)
                    if href.startswith('http'):
                        return href
                    return urljoin(base_url, href)

        link = soup.find('link', rel='next')
        if link and link.get('href'):
            href = link['href']
            if href.startswith('/'):
                return urljoin(base_url, href)
            return href
        return ''
