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
        db_module.init_crawl_log()
        self._last_crawl = db_module.get_last_crawl(self.conn, self.name)

    def set_llm(self, client):
        self.client = client

    def run(self, incremental: bool = True) -> int:
        """Execute crawl and return number of new articles."""
        if not self.conn:
            self.connect_db()

        import time as _time
        start_time = _time.time()
        run_id = db_module.log_run_start(self.conn, self.name)
        last_crawl = self._last_crawl

        print(f'\n[CRAWL] {self.name}: {self.url}')
        if last_crawl:
            print(f'  Last crawl: {last_crawl} (incremental)')

        articles = []
        error_msg = None
        try:
            if self.crawl_type == 'sitemap':
                articles = self._crawl_sitemap(incremental, last_crawl)
            elif self.crawl_type == 'atom':
                articles = self._crawl_atom()
            else:
                articles = self._crawl_listing(incremental)
        except Exception as e:
            error_msg = str(e)
            print(f'  ERROR: {e}')

        print(f'  Found {len(articles)} articles')
        if not error_msg:
            print(f'  Quantum-native (all {len(articles)} kept)')

        new_count = 0
        if not error_msg:
            for art in articles:
                if not db_module.is_new_url(self.conn, art['url']):
                    continue

                detail = self._fetch_detail(art['url'])
                raw_content = detail['content']
                pub_date = detail['date'] or art['date']
                best_title = art['title']
                if detail.get('title') and len(detail['title']) > len(best_title):
                    best_title = detail['title']

                # LLM cleaning: remove breadcrumbs, nav, social buttons, author lines
                content = raw_content
                if raw_content and self.client:
                    content = self._clean_content(raw_content, best_title)

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
        duration = _time.time() - start_time
        db_module.log_run_end(self.conn, run_id, len(articles), new_count, duration, error_msg)
        db_module.update_crawl_log(self.conn, self.name, len(articles), new_count)
        return new_count

    # ---- Internal: LLM content cleaning ----
    # Default tail noise patterns (can be overridden per source)
    DEFAULT_TAIL_PATTERNS = [
        r'\nView all posts by ',
        r'\nAbout the Author',
        r'\nAbout \w+ \w+\n',
        r'\nComments\n',
        r'\nComments are closed',
        r'\nShare this:',
        r'\nRelated posts:',
        r'\nRelated articles:',
        r'\nTags:',
        r'\nCategories:',
        r'\nPublished by ',
        r'\nPosted in ',
        r'\nLike this:',
        r'\nSubscribe to',
        r'\nNewsletter',
        r'\nYou may also like',
        r'\nRead more about',
        r'\nAuthor:',
    ]

    def _clean_tail(self, text: str) -> str:
        """Rule-based removal of common footer noise.
        Uses source-specific patterns if configured, otherwise defaults."""
        import re
        cut_patterns = self.source.get('tail_cut_patterns', self.DEFAULT_TAIL_PATTERNS)
        # Find the earliest cut point across all patterns
        cut_at = len(text)
        for pattern in cut_patterns:
            m = re.search(pattern, text)
            if m and m.start() < cut_at:
                cut_at = m.start()
        return text[:cut_at] if cut_at < len(text) else text

    def _clean_content(self, raw_text: str, title: str) -> str:
        """LLM clean head + rule-based clean tail."""
        # 1. Rule-based tail cleaning
        text = self._clean_tail(raw_text)

        # 2. Check if head needs LLM cleaning
        noise_patterns = ['skip to main content', 'breadcrumb', 'share this',
                          'english', '中文', 'like', 'discuss', 'L\nT\nF\nR\nE']
        text_head = text[:1000].lower().replace('\n', ' ')
        has_noise = sum(1 for p in noise_patterns if p in text_head) >= 2
        if not has_noise:
            return text

        # 3. LLM clean the head only
        try:
            head = text[:3000]
            middle = text[3000:] if len(text) > 3000 else ''
            clean_msg = [
                {"role": "system", "content": (
                    "你是文本清洗工具。删除以下文章中的导航面包屑、语言切换(English/中文)、"
                    "社交分享按钮(Like/Discuss/L/T/F/R/E)、作者署名行、\"Skip to main content\"。"
                    "保留文章标题和正文。不添加任何解释，只输出清洗后的文本。"
                )},
                {"role": "user", "content": f"标题：{title}\n\n{head}"},
            ]
            cleaned_head = self.client.chat(clean_msg).strip()
            if len(cleaned_head) < len(head) * 0.3:
                return text
            return cleaned_head + middle
        except Exception:
            return text

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
    def _crawl_sitemap(self, incremental: bool = True, last_crawl: str = None) -> list:
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
            skipped_old = 0
            for url_el in soup.find_all('url'):
                loc = (url_el.find('loc') or {}).text if url_el.find('loc') else ''
                lastmod = (url_el.find('lastmod') or {}).text if url_el.find('lastmod') else ''
                # Incremental: skip URLs older than last crawl
                if incremental and last_crawl and lastmod:
                    if lastmod[:10] <= last_crawl[:10]:
                        skipped_old += 1
                        continue
                if keyword in loc.lower() and is_article_url(loc):
                    slug = loc.rstrip('/').rsplit('/', 1)[-1].replace('-', ' ')
                    title = ' '.join(w[0].upper() + w[1:] if w else w for w in slug.split())
                    articles.append({'title': title, 'url': loc, 'date': lastmod[:10] if lastmod else ''})
            if skipped_old:
                print(f'  Skipped {skipped_old} old URLs (before {last_crawl[:10]})')
            return articles
        except Exception as e:
            print(f'  Error: {e}')
            return []

    # ---- Internal: HTML listing ----
    def _crawl_listing(self, incremental: bool = True) -> list:
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
                db_new = 0
                for art in new_articles:
                    if art['url'] not in seen_urls:
                        seen_urls.add(art['url'])
                        articles.append(art)
                        added += 1
                        if db_module.is_new_url(self.conn, art['url']):
                            db_new += 1
                # Incremental: stop when page has no genuinely new (not in DB) articles
                if incremental and db_new == 0:
                    break
                if added == 0:
                    break
            return articles
        except Exception as e:
            import traceback
            print(f'  Error: {e}')
            traceback.print_exc()
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
