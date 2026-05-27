"""Crawl Google Quantum AI news."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.base import BaseCrawler
from core.llm import get_llm

SOURCE = {
    "name": "Google Quantum AI",
    "type": "sitemap",
    "url": "https://blog.google/sitemap.xml",
    "url_pattern": "quantum",
    "quantum_native": True,
    "tail_cut_patterns": [
        r'\nRelated posts\n',
        r'\nQuantum computing\n',
        r'\nBy\n',
    ],
}

if __name__ == '__main__':
    crawler = BaseCrawler(SOURCE)
    crawler.connect_db()
    crawler.set_llm(get_llm())
    new_count = crawler.run()
    crawler.conn.close()
    print(f'[OK] {new_count} new articles from Google Quantum AI')
