import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.auth import AuthContext
from app.database import Base
from app.models import CreativeGeneration, CreativeProject, Product
from app.api import dashboard
from app.services.generation_summary import generation_summary_rows

class WorkspacePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.auth = AuthContext('user', 'tenant', 'owner', '', '')
        product = Product(tenant_id='tenant', name='product', image_urls=['image.png'])
        self.db.add(product)
        self.db.flush()
        self.product_id = product.id
        project = CreativeProject(tenant_id='tenant', product_id=product.id, name='project')
        self.db.add(project)
        self.db.flush()
        self.project_id = project.id
        for status, context in [
            ('completed', {}), ('failed', {'diagnostic': {'code': 'timeout', 'title': 'retry'}}),
            ('running', {'triggered_by': {'email': 'user@example.com'}, 'trigger_source': 'manual'}),
            ('cancelled', {}),
        ]:
            self.db.add(CreativeGeneration(tenant_id='tenant', project_id=project.id, status=status,
                provider='provider', result_node_ids=['node'] if status == 'completed' else [],
                duration_ms=100, context_snapshot={**context, 'large_history': 'x' * 100000},
                updated_at=datetime.utcnow()-timedelta(hours=1)))
        self.db.add(CreativeGeneration(tenant_id='other', project_id=project.id, status='failed'))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_dashboard_matches_full_history_without_loading_it(self):
        result = dashboard.get_stats(db=self.db, auth=self.auth)
        with patch('app.api.dashboard.generation_summary_rows', side_effect=lambda query: query.all()):
            expected = dashboard.get_stats(db=self.db, auth=self.auth)
        self.assertEqual(result, expected)
        self.assertEqual(result['summary']['failed_tasks'], 2)

    def test_generation_projection_omits_large_context_and_prompt(self):
        statements = []
        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            rows = generation_summary_rows(self.db.query(CreativeGeneration).filter_by(tenant_id='tenant'))
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)
        self.assertEqual(len(statements), 1)
        self.assertEqual(len(rows), 4)
        self.assertNotIn('creative_generations.prompt', statements[0])
        self.assertTrue(all('large_history' not in row.context_snapshot for row in rows))

if __name__ == '__main__':
    unittest.main()
