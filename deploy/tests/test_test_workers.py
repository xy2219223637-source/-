import importlib.util
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, '/app')
runtime_path = Path(os.environ.get('TEST_RUNTIME_PATH', str(Path(__file__).resolve().parents[1] / 'test-runtime/runtime.py')))
sys.path.insert(0, str(runtime_path.parent))
spec = importlib.util.spec_from_file_location('test_runtime', runtime_path)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
from app import main, models as m
from app.database import Base, SessionLocal, engine
from app.services import production_queue as pq, video_analysis_queue as vq
try:
    from app.services.video_repair import worker_v4 as repair
except ModuleNotFoundError:
    repair = None

Base.metadata.create_all(engine)
workers = runtime.branch_workers(main)
expected = {'start_worker','start_video_analysis_worker','start_qianchuan_worker'}
if repair is not None:
    expected.add('start_video_repair_worker')
assert set(workers) == expected
settings = SimpleNamespace(run_background_workers=False, run_startup_recovery=False,
                           video_repair_v4_enabled=True, video_repair_async_worker_enabled=True)
runtime.prepare_startup(main, main.settings, settings)
main.app.router.on_startup[0]()
assert all(getattr(sys.modules[function.__module__], '_thread') is None for function in workers.values())

# The enabled lifecycle must invoke every worker and skip automatic schema init.
main.app.router.on_startup[:] = [main.on_startup]
started = []
for name, function in workers.items():
    setattr(main, name, function)
settings.run_background_workers = settings.run_startup_recovery = True
runtime.prepare_startup(main, main.settings, settings)
for name in workers:
    setattr(main, name, lambda name=name: started.append(name))
main.app.router.on_startup[0]()
assert set(started) == set(workers)

with SessionLocal() as db:
    db.add(m.ProductionQueueTask(id='fixture', tenant_id='fixture', actor_id='fixture',
                                task_type='generate_detail_page', status='running', payload={'project_id':1}))
    db.add(m.VideoAsset(id=1, tenant_id='fixture', product_id=1, asset_id='fixture', filename='fixture.mp4', object_url='/fixture',
                        content_hash='fixture', status='analyzing'))
    if repair is not None:
        db.add(m.VideoRepairJob(id=1, tenant_id='fixture', task_id=999, job_type='ANALYZE',
                               idempotency_key='fixture', status='RUNNING'))
    db.commit()
# Stub provider-facing generation and analysis only. Actual polling, recovery,
# claiming and completion run from the deployed branch against temporary SQLite.
import app.api.creative as creative
creative.generate_plan = lambda *args, **kwargs: None
creative.generate_variants = lambda *args, **kwargs: None
def complete_asset(ident):
    with SessionLocal() as db:
        asset = db.get(m.VideoAsset, ident)
        asset.status = 'analyzed'
        db.commit()
vq._run_asset = complete_asset
pq.start_worker()
vq.start_video_analysis_worker()
if repair is not None:
    repair.start_video_repair_worker()
try:
    for attempt in range(100):
        with SessionLocal() as db:
            statuses = [db.get(m.ProductionQueueTask, 'fixture').status,
                        db.get(m.VideoAsset, 1).status]
            if repair is not None:
                statuses.append(db.get(m.VideoRepairJob, 1).status)
        if statuses == ['completed','analyzed'] + (['CANCELED'] if repair else []):
            break
        time.sleep(.1)
    else:
        raise AssertionError(statuses)
    runtime.prepare_health(main, settings, workers)
    health = next(route for route in main.app.routes if getattr(route,'path',None) == '/api/health').endpoint
    result = health()
    required = ['production_queue','video_analysis'] + (['video_repair'] if repair else [])
    assert result['status'] == 'ok' and all(result['background_workers'][name] for name in required)
    original = pq._thread
    pq._thread = SimpleNamespace(is_alive=lambda: False)
    assert health()['status'] == 'degraded'
    pq._thread = original
finally:
    pq.stop_worker()
    vq.stop_video_analysis_worker()
    if repair is not None:
        repair.stop_video_repair_worker()
    for module in [pq,vq] + ([repair] if repair else []):
        module._thread.join(timeout=3)
print('VALIDATED: startup enables all workers; real queues recover and consume tasks; health detects stopped workers')
