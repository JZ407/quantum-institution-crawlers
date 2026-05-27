"""Backfill Chinese summaries for existing institution articles using LLM."""
import sys, os, io, sqlite3, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from crawl_institutions import get_llm, DB_PATH

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("SELECT id, title, content FROM articles WHERE summary_cn IS NULL OR summary_cn = ''")
rows = c.fetchall()
conn.close()

print(f'Found {len(rows)} articles without Chinese summary')

if not rows:
    print('Nothing to do.')
    sys.exit(0)

client = get_llm()
updated = 0

for art_id, title, content in rows:
    if not content:
        continue
    short = title[:60]
    print(f'[{art_id}] {short} ... ', end='', flush=True)
    try:
        cn_msg = [
            {"role": "system", "content": "你是量子科技翻译专家。请将以下英文文章内容总结为一句话中文摘要（100字以内）。只输出中文，不要解释。"},
            {"role": "user", "content": f"标题：{title}\n\n内容：{content[:2000]}"},
        ]
        summary_cn = client.chat(cn_msg).strip()
        if len(summary_cn) > 200:
            summary_cn = summary_cn[:200]
        conn2 = sqlite3.connect(DB_PATH)
        conn2.execute('UPDATE articles SET summary_cn = ? WHERE id = ?', (summary_cn, art_id))
        conn2.commit()
        conn2.close()
        updated += 1
        print(summary_cn[:80])
    except Exception as e:
        print(f'ERROR: {e}')
    time.sleep(0.3)

print(f'\nDone: {updated}/{len(rows)} updated')
