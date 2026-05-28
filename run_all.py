"""Run all institution crawlers."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.llm import get_llm

SOURCES = [
    ('sources.ibm_quantum', 'IBM Quantum'),
    ('sources.quantinuum', 'Quantinuum Blog'),
    ('sources.quantinuum_press', 'Quantinuum Press'),
    ('sources.google_quantum_ai', 'Google Quantum AI'),
    ('sources.microsoft_azure_quantum', 'Microsoft Azure Quantum'),
    ('sources.nvidia_quantum', 'NVIDIA Quantum'),
    ('sources.ionq', 'IonQ'),
    ('sources.rigetti', 'Rigetti'),
    ('sources.psiquantum', 'PsiQuantum'),
    ('sources.oqc', 'OQC'),
    ('sources.q_ctrl', 'Q-CTRL'),
    ('sources.quera', 'QuEra'),
    ('sources.atom_computing', 'Atom Computing'),
    ('sources.qunasys', 'QunaSys'),
    ('sources.classiq', 'Classiq'),
]


def run_all():
    client = get_llm()
    total_new = 0
    for mod_name, display_name in SOURCES:
        try:
            mod = __import__(mod_name, fromlist=['SOURCE'])
            from core.base import BaseCrawler
            crawler = BaseCrawler(mod.SOURCE)
            crawler.connect_db()
            crawler.set_llm(client)
            new_count = crawler.run()
            crawler.conn.close()
            total_new += new_count
            time.sleep(1)
        except Exception as e:
            print(f'  ERROR ({display_name}): {e}')
    print(f'\n[OK] {total_new} new articles total')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--log':
        from core.db import view_log
        source = sys.argv[2] if len(sys.argv) > 2 else None
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 20
        view_log(limit, source)
    elif len(sys.argv) > 1:
        # Run specific institution
        name = sys.argv[1].lower()
        matched = [(m, d) for m, d in SOURCES if name in d.lower()]
        if matched:
            mod_name, display_name = matched[0]
            mod = __import__(mod_name, fromlist=['SOURCE'])
            from core.base import BaseCrawler
            crawler = BaseCrawler(mod.SOURCE)
            crawler.connect_db()
            crawler.set_llm(get_llm())
            new_count = crawler.run()
            crawler.conn.close()
            print(f'\n[OK] {new_count} new articles from {display_name}')
        else:
            print(f'No source matching "{sys.argv[1]}"')
    else:
        run_all()
