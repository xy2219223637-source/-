"""Exercise both real branch applications using an isolated disposable database."""
import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, '/app')
sys.path.insert(0, os.environ.get('ISOLATION_RUNTIME_DIR', str(Path(__file__).resolve().parents[1] / 'shared-runtime')))
import task_isolation_runtime as runtime
runtime.install()
from sqlalchemy import update
from app.database import Base, engine, SessionLocal
from app import models as m
from app.services import production_queue as pq, video_analysis_queue as vq
from app.services.qianchuan.uploads import UploadService
from app.services.qianchuan.service import QianchuanService

if os.environ.get('ISOLATION_TEST_POSTGRES') == '1':
    from sqlalchemy import event
    @event.listens_for(engine, 'connect')
    def fixture_constraints(connection, record):
        # This isolated PostgreSQL container contains synthetic fixtures only.
        with connection.cursor() as cursor:
            cursor.execute('SET session_replication_role = replica')

service = runtime.SERVICE
other = 'artinjecter' if service == 'production' else 'production'
phase = sys.argv[1]


def fixture(model, ident, status, **overrides):
    # Populate required fixture columns only; defaults remain the actual branch's defaults.
    values = {}
    for col in model.__table__.columns:
        if col.default is not None or col.server_default is not None or col.nullable:
            continue
        if col.primary_key:
            values[col.name] = ident
        elif col.type.python_type is str:
            values[col.name] = 'isolation-fixture'
        elif col.type.python_type is int:
            values[col.name] = 1
        elif col.type.python_type is bool:
            values[col.name] = False
        elif col.type.python_type is float:
            values[col.name] = 0.0
        elif col.type.python_type is datetime:
            values[col.name] = datetime.utcnow()
    values.update(id=ident, status=status)
    values.update(overrides)
    return model(**values)


if phase == 'migrate':
    from sqlalchemy import inspect, text
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for table in runtime.MODELS:
            connection.execute(text('ALTER TABLE "' + table + '" DROP COLUMN deployment_service'))
    runtime.migrate()
    runtime.migrate()
    with engine.connect() as connection:
        for table in runtime.MODELS:
            col = next(col for col in inspect(connection).get_columns(table) if col['name'] == runtime.OWNER)
            assert not col['nullable'] and 'production' in col['default']
    print('PostgreSQL ownership migration and repeat migration passed')

elif phase == 'seed':
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        queue = fixture(m.ProductionQueueTask, service, 'running')
        video = fixture(m.VideoAsset, 1 if service == 'production' else 2, 'analyzing',
                        asset_id=service, filename=service + '.mp4', object_url='/fixture', content_hash=service)
        analysis = fixture(m.VideoAssetAnalysis, 1 if service == 'production' else 2, 'analyzing', asset_id=video.id)
        creative = fixture(m.CreativeGeneration, 1 if service == 'production' else 2, 'running',
                           updated_at=datetime.utcnow() - timedelta(hours=1))
        batch = fixture(m.CreativeBatchJob, service, 'running')
        conn = fixture(m.QianchuanConnection, service, 'syncing')
        oauth = fixture(m.QianchuanOAuthSession, service, 'processing',
                        state_hash=service, connection_id=service, browser_nonce_hash=service)
        upload = fixture(m.QianchuanUpload, service, 'running', phase='preparing', binding_id=service,
                         created_by=service, source_content_hash=service, advertiser_id=service)
        db.add_all([queue, video, analysis, creative, batch, conn, oauth, upload])
        db.commit()
        assert all(row.deployment_service == service for row in [queue, video, analysis, creative, batch, conn, oauth, upload])
    print(service + ': all eight task models record origin atomically')

elif phase == 'recover':
    def foreign_snapshot():
        with SessionLocal() as db:
            return {
                name: [(str(row.id), row.status) for row in db.query(model).filter(model.deployment_service == other).order_by(model.id)]
                for name, model in runtime.MODELS.items()
            }
    before = foreign_snapshot()
    import app.main as main
    original_init = main.init_db
    main.init_db = lambda: None
    main.start_worker = lambda: None
    main.start_video_analysis_worker = lambda: None
    main.start_qianchuan_worker = lambda: None
    main.on_startup()
    # Exercise actual worker recovery without starting provider-facing worker threads.
    original_loop = pq._loop
    pq._loop = lambda: None
    pq.start_worker()
    pq._thread.join(timeout=5)
    with SessionLocal() as db:
        vq.recover_interrupted_video_analyses(db)
    qc = QianchuanService.__new__(QianchuanService)
    qc.session_factory = SessionLocal
    qc.recover()
    uploads = UploadService(SimpleNamespace(client=None, session_factory=SessionLocal))
    uploads.recover()
    assert foreign_snapshot() == before
    with SessionLocal() as db:
        assert db.get(m.ProductionQueueTask, service).status == 'pending'
        assert db.get(m.VideoAsset, 1 if service == 'production' else 2).status == 'queued_for_analysis'
        assert db.get(m.CreativeBatchJob, service).status == 'interrupted'
        assert db.get(m.CreativeGeneration, 1 if service == 'production' else 2).status == 'failed'
        assert db.get(m.QianchuanConnection, service).status == 'sync_pending'
        assert db.get(m.QianchuanUpload, service).status == 'queued'
        if service == 'production':
            for model, ident in [(m.ProductionQueueTask, other), (m.CreativeBatchJob, other),
                                 (m.CreativeGeneration, 2), (m.VideoAsset, 2), (m.VideoAssetAnalysis, 2),
                                 (m.QianchuanUpload, other)]:
                expected = 'analyzing' if model in {m.VideoAsset, m.VideoAssetAnalysis} else 'running'
                assert db.get(model, ident).status == expected, model.__name__
            assert db.get(m.QianchuanConnection, other).status == 'syncing'
            assert db.get(m.QianchuanOAuthSession, other).status == 'processing'
    print(service + ': actual startup and all worker recovery leave other deployment untouched')

elif phase == 'select':
    expected_id = 1 if service == 'production' else 2
    assert vq._next_asset_id() == expected_id
    uploads = UploadService(SimpleNamespace(client=None, session_factory=SessionLocal))
    claimed = []
    uploads.send = lambda ident: claimed.append(ident)
    uploads.verify = lambda ident: claimed.append(ident)
    assert uploads.run_next()
    assert claimed == [service]
    # Each original branch production loop must select its own durable task.
    called = []
    pq._stop.clear()
    def fake_run(ident):
        called.append(ident)
        pq._stop.set()
    pq._run_task = fake_run
    pq.task_lock = lambda ident: None
    pq._loop()
    assert called == [service]
    print(service + ': video, production and upload polling select only own tasks')

elif phase == 'routing':
    from fastapi import HTTPException
    with SessionLocal() as db:
        foreign = db.get(m.VideoAsset, 2 if service == 'production' else 1)
        # A terminal asset may be reanalyzed from either site and receives the new request's owner.
        foreign.status = 'ready'
        db.commit()
        foreign.status = 'queued_for_analysis'
        db.commit()
        assert foreign.deployment_service == service
        db.execute(update(m.QianchuanConnection).where(m.QianchuanConnection.id == other).values(status='active'))
        db.commit()
        db.execute(update(m.QianchuanConnection).where(m.QianchuanConnection.id == other).values(status='sync_pending'))
        db.commit()
        assert db.get(m.QianchuanConnection, other).deployment_service == service
        # Simulate the other deployment owning an active task; transfer must be blocked.
        db.execute(m.VideoAsset.__table__.update().where(m.VideoAsset.id == foreign.id).values(
            status='analyzing', deployment_service=other))
        db.commit()
        db.expire_all()
        foreign = db.get(m.VideoAsset, foreign.id)
        foreign.status = 'queued_for_analysis'
        try:
            db.commit()
        except HTTPException as exc:
            assert exc.status_code == 409
            db.rollback()
        else:
            raise AssertionError('Cross-deployment active job was transferred')
        db.execute(m.QianchuanConnection.__table__.update().where(m.QianchuanConnection.id == other).values(
            status='syncing', deployment_service=other))
        db.commit()
        blocked = db.execute(update(m.QianchuanConnection).where(m.QianchuanConnection.id == other).values(status='sync_pending')).rowcount
        assert blocked == 0
        db.rollback()
        # A manual verification retry after automatic verification is exhausted
        # belongs to the requesting deployment, without resending the upload.
        db.execute(m.QianchuanUpload.__table__.update().where(m.QianchuanUpload.id == other).values(
            status='unknown', deployment_service=other, verify_attempts=3, next_attempt_at=None))
        db.commit()
        db.expire_all()
        retry = db.get(m.QianchuanUpload, other)
        retry.verify_attempts = 0
        retry.next_attempt_at = datetime.utcnow()
        db.commit()
        assert retry.deployment_service == service
    from app.services.redis_client import redis_key
    assert ('deployment:' + service + ':') in redis_key('production-queue:wake')
    assert ('deployment:' + service + ':') in redis_key('video-analysis:wake')
    assert ('deployment:' + service + ':') not in redis_key('shared-cache')
    print(service + ': ORM and bulk retries route ownership; foreign active task transfer blocked; wakeups isolated')
else:
    raise SystemExit('Unknown phase')
