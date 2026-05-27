"""Crawl OQC news."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.base import BaseCrawler
from core.llm import get_llm

SOURCE = {
    "name": "OQC",
    "type": "enterprise",
    "url": "https://oqc.tech/company/newsroom/",
    "url_pattern": "/company/newsroom/",
    "quantum_native": True,
    "max_pages": 8,
    "tail_cut_patterns": [
        r'\nYOU MAY ALSO BE INTERESTED IN',
        r'\nThe latest from the Newsroom',
    ],
}

if __name__ == '__main__':
    crawler = BaseCrawler(SOURCE)
    crawler.connect_db()
    crawler.set_llm(get_llm())
    new_count = crawler.run()
    crawler.conn.close()
    print(f'[OK] {new_count} new articles from OQC')
