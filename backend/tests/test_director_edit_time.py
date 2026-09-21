import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import director
from app.auth import AuthContext, current_auth
from app.database import Base, get_db, migrate_director_edit_time
from app.models import DirectorAsset, DirectorProject, DirectorSection, DirectorShot


class DirectorEditTimeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        app = FastAPI()
        app.include_router(director.router, prefix='/api')

        def database():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[current_auth] = lambda: AuthContext(
            user_id='user', tenant_id='tenant', role='owner', email='test@example.com', tenant_name='Test')
        self.client = TestClient(app)
        with self.sessions() as db:
            project = DirectorProject(tenant_id='tenant', name='Test', status='ready')
            db.add(project)
            db.flush()
            section = DirectorSection(tenant_id='tenant', project_id=project.id, title='Section')
            db.add(section)
            db.flush()
            shot = DirectorShot(tenant_id='tenant', project_id=project.id, section_id=section.id)
            db.add(shot)
            db.commit()
            self.project_id, self.section_id, self.shot_id = project.id, section.id, shot.id
        self.url = f'/api/director-projects/{self.project_id}'

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def test_repeated_section_edits_noop_and_rejected_save(self):
        payload = dict(id=self.section_id, sort_order=0, title='First edit', start_ms=0, end_ms=0)
        for day in (1, 2):
            payload['title'] = f'Edit {day}'
            when = datetime(2026, 9, day, 8, 30)
            with patch.object(director, '_utcnow', return_value=when):
                response = self.client.put(self.url + '/sections', json=[payload])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['last_edited_at'], when.isoformat() + '+00:00')
        saved = response.json()['last_edited_at']
        self.assertEqual(self.client.put(self.url + '/sections', json=[payload]).json()['last_edited_at'], saved)
        payload['id'] = 99999
        self.assertEqual(self.client.put(self.url + '/sections', json=[payload]).status_code, 404)
        self.assertEqual(self.client.get(self.url).json()['last_edited_at'], saved)

    def test_shot_edits_and_background_status_are_separate(self):
        empty = self.client.put(self.url + '/shots', json=[{'id': self.shot_id}]).json()
        self.assertIsNone(empty['last_edited_at'])
        payload = {'id': self.shot_id, 'transcript': 'New script'}
        response = self.client.put(self.url + '/shots', json=[payload])
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()['last_edited_at']
        self.assertIsNotNone(saved)
        self.assertEqual(self.client.put(self.url + '/shots', json=[payload]).json()['last_edited_at'], saved)
        with self.sessions() as db:
            db.get(DirectorProject, self.project_id).status = 'analyzing'
            db.commit()
        self.assertEqual(self.client.get(self.url).json()['last_edited_at'], saved)

    def test_material_collection_and_removal_update_time(self):
        with self.sessions() as db:
            db.add(DirectorAsset(tenant_id='tenant', project_id=self.project_id, section_id=self.section_id,
                                 role='reference', source='collected', media_kind='video', url='/reference.mp4'))
            db.commit()
        response = self.client.post(self.url + '/collect-materials')
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()['last_edited_at']
        self.assertIsNotNone(saved)
        repeated = self.client.post(self.url + '/collect-materials').json()
        self.assertEqual(repeated['collected_count'], 0)
        self.assertEqual(repeated['last_edited_at'], saved)
        material = next(a for a in repeated['sections'][0]['assets'] if a['role'] == 'material')
        self.assertEqual(self.client.delete(self.url + f"/assets/{material['id']}").status_code, 200)
        self.assertNotEqual(self.client.get(self.url).json()['last_edited_at'], saved)

    def test_list_order_and_new_project_time_are_not_changed_by_processing(self):
        now = datetime(2026, 9, 21)
        with self.sessions() as db:
            project = db.get(DirectorProject, self.project_id)
            project.created_at = now - timedelta(days=2)
            project.last_edited_at = now
            db.add(DirectorProject(tenant_id='tenant', name='New', created_at=now - timedelta(days=1),
                                   updated_at=now + timedelta(days=1)))
            db.commit()
        rows = self.client.get('/api/director-projects').json()
        self.assertEqual(rows[0]['id'], self.project_id)
        self.assertTrue(rows[1]['edit_time_tracked'])
        self.assertIsNone(rows[1]['last_edited_at'])
        self.assertTrue(rows[1]['created_at'].endswith('+00:00'))

    def test_legacy_migration_is_idempotent_and_does_not_invent_edits(self):
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE director_projects (id INTEGER PRIMARY KEY)'))
            connection.execute(text('INSERT INTO director_projects (id) VALUES (1)'))
        migrate_director_edit_time(engine)
        migrate_director_edit_time(engine)
        with engine.connect() as connection:
            row = connection.execute(text('SELECT last_edited_at, edit_time_tracked FROM director_projects')).one()
            self.assertEqual(tuple(row), (None, 0))
        engine.dispose()
