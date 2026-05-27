"""Crawl Rigetti news."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.base import BaseCrawler
from core.llm import get_llm

SOURCE = {
    "name": "Rigetti",
    "type": "sitemap",
    "url": "https://www.rigetti.com/sitemaps-1-sitemap.xml",
    "url_pattern": "quantum",
    "quantum_native": True,
}

if __name__ == '__main__':
    crawler = BaseCrawler(SOURCE)
    crawler.connect_db()
    crawler.set_llm(get_llm())
    new_count = crawler.run()
    crawler.conn.close()
    print(f'[OK] {new_count} new articles from Rigetti')
