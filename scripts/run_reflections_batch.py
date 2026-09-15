"""批量跑感悟报告评审（reflection_pipeline），每次10篇，支持断点续跑。"""
import sys, io, os, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = sys.stdout

# Add project root to path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

os.environ.setdefault('PAPERFORGE_DB_PATH', 'mock_api/paperforge_mock.db')
from mock_api.database import init_db, SessionLocal
from mock_api.models import Paper, DepthReviewV4
from mock_api.depth_tasks import run_depth_reflection_sync
from sqlalchemy import func

os.environ.setdefault('PAPERFORGE_LLM_WATCHDOG_TIMEOUT', '600')
os.environ.setdefault('PAPERFORGE_LOCAL_TIMEOUT_CAP', '580')
os.environ.setdefault('PAPERFORGE_LLM_REQUEST_TIMEOUT', '580')
os.environ.setdefault('PAPERFORGE_REFLECTION_II_SAMPLES', '1')  # 关闭 II 中位数采样，每篇省 ~150s

init_db()

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
todo_file = os.path.join(_root, 'tmp', 'todo_reflections.txt')
progress_file = os.path.join(_root, 'tmp', 'reflection_progress.json')
os.makedirs(os.path.join(_root, 'tmp'), exist_ok=True)

# Load progress
done_pids = set()
if os.path.exists(progress_file):
    with open(progress_file) as f:
        done_pids = set(json.load(f).get('done', []))

# Load todo
with open(todo_file) as f:
    all_todo = [line.strip() for line in f if line.strip()]

remaining = [p for p in all_todo if p not in done_pids]
print(f'总待处理: {len(all_todo)}, 已完成: {len(done_pids)}, 剩余: {len(remaining)}')

if not remaining:
    print('全部完成！')
    sys.exit(0)

batch = remaining
print(f'\n本批处理: {len(batch)} 篇（全部剩余）')
print('=' * 60)

t_start = time.time()
results_batch = []

for i, pid in enumerate(batch):
    t0 = time.time()
    try:
        run_depth_reflection_sync(pid)
        session = SessionLocal()
        try:
            rec = session.query(DepthReviewV4).filter(
                DepthReviewV4.paper_id == pid
            ).order_by(DepthReviewV4.created_at.desc()).first()
            rr = rec.reflection_result if rec and isinstance(rec.reflection_result, dict) else {}
        finally:
            session.close()
        
        scores = rr.get('scores', {})
        ua = scores.get('understanding_accuracy', -1)
        verdict = rr.get('verdict', '?')
        elapsed = time.time() - t0
        
        if ua is not None and ua >= 0:
            ad = scores.get('analysis_depth', 0) or 0
            ii = scores.get('innovative_insights', 0) or 0
            es = scores.get('evidence_support', 0) or 0
            fid = scores.get('fidelity', 0) or 0
            cov = scores.get('coverage', 0) or 0
            avg = rr.get('average', 0) or 0
            print(f'  ✅ [{i+1}/{len(batch)}] {pid} UA={ua:.3f} AD={ad:.3f} II={ii:.3f} FID={fid:.3f} AVG={avg:.3f} {verdict} ({elapsed:.0f}s)', flush=True)
            done_pids.add(pid)
        else:
            print(f'  ⚠️ [{i+1}/{len(batch)}] {pid} scores缺失 status={rec.status} ({elapsed:.0f}s)', flush=True)
            done_pids.add(pid)  # Mark done to avoid retry loop
        
        results_batch.append({'pid': pid, 'ok': ua >= 0, 'time': elapsed})
        
    except Exception as e:
        elapsed = time.time() - t0
        print(f'  ❌ [{i+1}/{len(batch)}] {pid} FAIL ({elapsed:.0f}s): {str(e)[:100]}')
        results_batch.append({'pid': pid, 'ok': False, 'time': elapsed, 'error': str(e)[:100]})
    
    # Save progress after each paper
    with open(progress_file, 'w') as f:
        json.dump({'done': list(done_pids)}, f)


elapsed = time.time() - t_start
ok_count = sum(1 for r in results_batch if r['ok'])
fail_count = len(results_batch) - ok_count
print(f'\n本批完成: {ok_count}/{len(batch)} 成功, {fail_count} 失败, 耗时 {elapsed/60:.1f} 分钟')
print(f'总进度: {len(done_pids)}/{len(all_todo)} ({len(done_pids)/len(all_todo)*100:.0f}%)')
