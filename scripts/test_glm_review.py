"""GLM-4.7-Flash 真实评审测试：对 8081 发一篇感悟报告评审，验证 JSON 输出质量。"""
import requests
import sqlite3
import time

conn = sqlite3.connect("mock_api/paperforge_mock.db")
cur = conn.cursor()
cur.execute(
    """SELECT p.id, substr(p.full_text,1,2500), substr(s.full_text,1,3000)
       FROM papers p JOIN papers s ON s.id = p.source_paper_id
       WHERE p.category='report' AND s.full_text IS NOT NULL AND length(s.full_text)>1000
       ORDER BY p.id LIMIT 1"""
)
rid, rep, paper = cur.fetchone()
conn.close()

json_schema = """{'claims': '报告中的关键论断列表', 'evidence': '每条论断对应的论文证据', 'scores': {'understanding_accuracy': 0-1, 'analysis_depth': 0-1, 'innovative_insights': 0-1, 'evidence_support': 0-1}, 'verdict_suggestion': 'well_done|needs_evidence|needs_depth|rewrite_required', 'comment': '总评'}"""
prompt = (
    "你是一位严谨的学术评审专家。请基于论文原文评估学生的感悟报告质量。\n\n"
    f"[论文原文]\n{paper}\n\n[学生感悟报告]\n{rep}\n\n"
    f"请从以下 4 个维度评分（0-1），并输出严格 JSON：\n{json_schema}"
)

t0 = time.time()
r = requests.post(
    "http://127.0.0.1:8081/v1/chat/completions",
    json={
        "model": "local",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1500,
        "temperature": 0.0,
    },
    timeout=600,
)
dt = time.time() - t0
u = r.json().get("usage", {})
out = r.json()["choices"][0]["message"]["content"]
print(f"报告 {rid} | prompt {u.get('prompt_tokens')} tok + {u.get('completion_tokens')} tok | 总 {dt:.1f}s")
print("---输出前 500 字---")
print(out[:500])
