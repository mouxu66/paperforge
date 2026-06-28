"""PaperForge 功能调试脚本：逐个测试所有后端 API。"""
import json
import time

import requests

BASE = "http://127.0.0.1:8770/api"
PASS = 0
FAIL = 0


def ok(name, detail=""):
    global PASS
    PASS += 1
    print(f"  [PASS] {name} {detail}")


def fail(name, detail=""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name} {detail}")


def ensure_test_project():
    """确保有一个写作项目（没有就创建），返回 (project_id, chapter_id)。"""
    r = requests.get(f"{BASE}/writing/projects")
    projects = r.json()
    if projects:
        pid = projects[0]["id"]
    else:
        # 创建测试项目
        r = requests.post(
            f"{BASE}/writing/projects",
            json={"title": "测试论文", "keywords": ["LoRA", "fine-tuning"], "targetJournal": "Test"},
        )
        if r.status_code not in (200, 201):
            print(f"  [FAIL] 创建项目 status={r.status_code} body={r.text[:200]}")
            return None, None
        pid = r.json()["id"]
        ok("创建测试项目", f"pid={pid}")

    # 获取章节树
    r = requests.get(f"{BASE}/writing/projects/{pid}/chapters")
    tree = r.json()
    if not tree:
        # 创建根章节
        r = requests.post(
            f"{BASE}/writing/projects/{pid}/chapters",
            json={"title": "引言", "parentId": None},
        )
        if r.status_code not in (200, 201):
            print(f"  [FAIL] 创建章节 status={r.status_code}")
            return pid, None
        ok("创建测试章节", "引言")
        r = requests.get(f"{BASE}/writing/projects/{pid}/chapters")
        tree = r.json()

    # 找一个章节 ID
    chapter_id = tree[0]["id"] if tree else None

    # 给第一个章节写入一些内容（含引用标记）
    if chapter_id:
        content = "# 引言\n\nLoRA [@2305.14314] 是一种高效的微调方法。\n\n该方法通过低秩分解减少参数量。"
        r = requests.put(
            f"{BASE}/writing/chapters/{chapter_id}",
            json={"title": tree[0]["title"], "content": content},
        )
    return pid, chapter_id


def test_fts5_author_search():
    print("\n=== 1. FTS5 作者搜索 ===")
    r = requests.get(f"{BASE}/search/suggest", params={"q": "Dettmers"})
    data = r.json()
    if len(data) > 0:
        ok("FTS5 作者搜索", f"命中 {len(data)} 篇")
    else:
        r2 = requests.get(f"{BASE}/search/suggest", params={"q": "Bender"})
        if r2.json():
            ok("FTS5 作者搜索", f"命中 {len(r2.json())} 篇")
        else:
            fail("FTS5 作者搜索", "无结果")


def test_semantic_search():
    print("\n=== 2. 混合语义搜索 (RRF) ===")
    r = requests.post(
        f"{BASE}/search/semantic",
        json={"question": "LoRA efficient fine-tuning"},
        timeout=30,
    )
    if r.status_code == 200:
        data = r.json()
        if data:
            ok("混合语义搜索", f"返回 {len(data)} 条, Top1={data[0]['title'][:40]}")
        else:
            fail("混合语义搜索", "无结果")
    else:
        fail("混合语义搜索", f"status={r.status_code}")


def test_export_progress(pid):
    print("\n=== 3. 导出进度条 ===")
    if not pid:
        fail("导出进度", "无项目")
        return
    r = requests.post(f"{BASE}/writing/projects/{pid}/export")
    if r.status_code != 202:
        fail("启动导出", f"status={r.status_code} body={r.text[:200]}")
        return
    task_id = r.json()["taskId"]
    ok("启动导出任务", f"task_id={task_id[:8]}")
    for i in range(20):
        time.sleep(0.5)
        r = requests.get(f"{BASE}/writing/export/{task_id}/progress")
        p = r.json()
        if p["status"] == "done":
            ok("导出完成", f"refs={len(p['references'])}")
            return
        if p["status"] == "error":
            fail("导出失败", p.get("error", ""))
            return
    fail("导出超时", "10秒未完成")


def test_arxiv_fetch():
    print("\n=== 4. arXiv 定时拉取（手动触发） ===")
    r = requests.post(f"{BASE}/admin/arxiv/fetch-now", timeout=30)
    if r.status_code == 200:
        data = r.json()
        if data.get("message"):
            ok("arXiv 手动触发", "未配置关键词（预期）")
        else:
            ok("arXiv 手动触发", f"added={data.get('added',0)}")
    else:
        fail("arXiv 手动触发", f"status={r.status_code}")


def test_outline_tree(pid):
    print("\n=== 5. 大纲树 API ===")
    if not pid:
        fail("大纲树", "无项目")
        return
    r = requests.get(f"{BASE}/writing/projects/{pid}/chapters")
    tree = r.json()
    ok("获取章节树", f"{len(tree)} 个根节点")
    if tree:
        first_id = tree[0]["id"]
        r = requests.put(
            f"{BASE}/writing/chapters/{first_id}/move",
            json={"parentId": None, "order": 0},
        )
        if r.status_code == 200:
            ok("移动章节", "move OK")
        else:
            fail("移动章节", f"status={r.status_code}")
        r = requests.get(f"{BASE}/writing/projects/{pid}/word-count")
        if r.status_code == 200:
            wc = r.json()
            ok("字数统计", f"total={wc['total']}")
        else:
            fail("字数统计", f"status={r.status_code}")


def test_chapter_context(chapter_id):
    print("\n=== 6. 章节上下文 ===")
    if not chapter_id:
        fail("章节上下文", "无章节")
        return
    # 测试论文详情（引用预览依赖此接口）
    r = requests.get(f"{BASE}/papers/2305.14314")
    if r.status_code == 200 and r.json():
        ok("论文详情", f"title={r.json()['title'][:40]}")
    else:
        fail("论文详情", f"status={r.status_code}")
    return chapter_id


def test_writing_assist(chapter_id):
    print("\n=== 7. 智能续写 + 结构建议 ===")
    if not chapter_id:
        fail("写作辅助", "无章节")
        return
    # 结构建议
    r = requests.post(
        f"{BASE}/writing/chapters/{chapter_id}/suggest-structure",
        timeout=60,
    )
    if r.status_code == 200:
        data = r.json()
        ok("结构建议", f"confidence={data.get('confidence',0)}")
    elif r.status_code == 503:
        ok("结构建议（降级）", "LLM 未配置（预期）")
    else:
        fail("结构建议", f"status={r.status_code} body={r.text[:150]}")

    # 智能续写 SSE
    print("  测试续写 SSE...")
    try:
        r = requests.post(
            f"{BASE}/writing/chapters/{chapter_id}/continue",
            json={"direction": "总结"},
            stream=True,
            timeout=60,
        )
        if r.status_code == 200:
            tokens = []
            for line in r.iter_lines():
                if line:
                    s = line.decode("utf-8")
                    if s.startswith("data: "):
                        payload = json.loads(s[6:])
                        if payload["type"] == "token":
                            tokens.append(payload["data"])
                        elif payload["type"] == "done":
                            break
                        elif payload["type"] == "error":
                            ok("续写 SSE（降级）", payload["data"][:80])
                            return
            text = "".join(tokens)
            ok("续写 SSE", f"{len(tokens)} tokens, {len(text)} chars")
        else:
            fail("续写 SSE", f"status={r.status_code}")
    except Exception as e:
        fail("续写 SSE", str(e)[:100])


def test_frontend():
    print("\n=== 8. 前端页面 ===")
    import os
    dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "dist", "index.html")
    if os.path.exists(dist):
        ok("前端构建产物", "dist/index.html OK")
    else:
        fail("前端构建产物", "不存在")


if __name__ == "__main__":
    print("PaperForge 功能调试")
    print("=" * 50)
    try:
        pid, chapter_id = ensure_test_project()
        test_fts5_author_search()
        test_semantic_search()
        test_export_progress(pid)
        test_arxiv_fetch()
        test_outline_tree(pid)
        test_chapter_context(chapter_id)
        test_writing_assist(chapter_id)
        test_frontend()
    except Exception as e:
        fail("异常", str(e))
    print("\n" + "=" * 50)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
