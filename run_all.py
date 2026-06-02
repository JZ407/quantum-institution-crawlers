"""Run all institution crawlers - each source runs standalone."""
import sys, os, time, runpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SOURCES = [
    ('sources.ibm_quantum_blog', 'IBM Quantum Blog'),
    ('sources.ibm_quantum_pr', 'IBM Quantum PR'),
    ('sources.quantinuum_blog', 'Quantinuum Blog'),
    ('sources.quantinuum_press', 'Quantinuum Press'),
    ('sources.google_quantum_blog', 'Google Quantum AI'),
    ('sources.google_quantum_research', 'Google Quantum Research'),
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
    ('sources.alice_bob_blog', 'Alice & Bob Blog'),
    ('sources.alice_bob_newsroom', 'Alice & Bob Newsroom'),
]


def run_all():
    total_ok = 0
    total_fail = 0
    for mod_name, display_name in SOURCES:
        try:
            print(f'\n{"="*50}')
            runpy.run_module(mod_name, run_name='__main__')
            total_ok += 1
            time.sleep(1)
        except Exception as e:
            total_fail += 1
            print(f'  ERROR ({display_name}): {e}')
    print(f'\n[OK] {total_ok} succeeded, {total_fail} failed')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--log':
        from core.db import view_log
        source = sys.argv[2] if len(sys.argv) > 2 else None
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 20
        view_log(limit, source)
    elif len(sys.argv) > 1:
        name = sys.argv[1].lower()
        matched = [(m, d) for m, d in SOURCES if name in d.lower()]
        if matched:
            mod_name, display_name = matched[0]
            runpy.run_module(mod_name, run_name='__main__')
        else:
            print(f'No source matching "{sys.argv[1]}"')
    else:
        run_all()
