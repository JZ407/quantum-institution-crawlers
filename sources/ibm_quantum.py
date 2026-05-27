"""Crawl IBM Quantum news."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.base import BaseCrawler
from core.llm import get_llm

SOURCE = {
    "name": "IBM Quantum",
    "type": "enterprise",
    "url": "https://www.ibm.com/quantum/blog",
    "url_pattern": "/quantum/blog",
    "quantum_native": True,
    "max_pages": 5,
    "page_url_template": "?page={n}",
}

if __name__ == '__main__':
    crawler = BaseCrawler(SOURCE)
    crawler.connect_db()
    crawler.set_llm(get_llm())
    new_count = crawler.run()
    crawler.conn.close()
    print(f'[OK] {new_count} new articles from IBM Quantum')
