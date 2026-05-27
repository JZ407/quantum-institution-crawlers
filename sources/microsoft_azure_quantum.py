"""Crawl Microsoft Azure Quantum news."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.base import BaseCrawler
from core.llm import get_llm

SOURCE = {
    "name": "Microsoft Azure Quantum",
    "type": "enterprise",
    "url": "https://azure.microsoft.com/en-us/blog/quantum/",
    "url_pattern": "/quantum/",
    "quantum_native": True,
    "max_pages": 10,
    "page_url_template": "page/{n}/",
    "tail_cut_patterns": [
        r'\nNews\n',
        r'\nAzure Quantum Elements\n',
    ],
}

if __name__ == '__main__':
    crawler = BaseCrawler(SOURCE)
    crawler.connect_db()
    crawler.set_llm(get_llm())
    new_count = crawler.run()
    crawler.conn.close()
    print(f'[OK] {new_count} new articles from Microsoft Azure Quantum')
