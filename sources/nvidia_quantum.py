"""Crawl NVIDIA Quantum news."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.base import BaseCrawler
from core.llm import get_llm

SOURCE = {
    "name": "NVIDIA Quantum",
    "type": "atom",
    "url": "https://developer.nvidia.com/blog/tag/quantum-computing/feed/",
    "url_pattern": "/blog/",
    "quantum_native": True,
    "tail_cut_patterns": [
        r'\nLike\n',
        r'\nTags\n',
    ],
}

if __name__ == '__main__':
    crawler = BaseCrawler(SOURCE)
    crawler.connect_db()
    crawler.set_llm(get_llm())
    new_count = crawler.run()
    crawler.conn.close()
    print(f'[OK] {new_count} new articles from NVIDIA Quantum')
