"""缺陷代码同步逻辑与记录页（不连 Oracle）。"""
from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask

from app.backend_schedule.FT_DEFECT_CODE_SYNC_sche import (
    _SOURCE_ARRAYSIZE,
    fetch_source_rows,
)
from app.backend_schedule.defect_code_sync import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    ChangePlan,
    ExistingDefect,
    SourceRow,
    Winner,
    group_source,
    plan_changes,
    run_product_sync,
)
from app.config import Config
from app.controllers.defect_code_sync_ctrl import query_sync_logs
from app.routes.auth_routes import auth_bp
from app.routes.defect_code_sync_routes import defect_sync_bp


_APP_DIR = Path(__file__).resolve().parents[1] / 'app'
_FT = Config.DEFECT_SYNC_FT_CLASSIFY
_WLT = Config.DEFECT_SYNC_WLT_CLASSIFY
_IGNORE = set(Config.DEFECT_SYNC_IGNORE)


def _line_of(classify):
    if classify == _FT:
        return 'FT'
    if classify == _WLT:
        return 'WLT'
    return ''


def _row(classify, product, grade, code, name, updated=None):
    return SourceRow(classify, product, grade, code, name, updated)


class DedupeAndPlanTest(unittest.TestCase):
    def test_latest_update_time_wins_and_keeps_bsl_row(self):
        rows = [
            _row(_FT, 'GC02M1-3.5', 'FAIL', '10', 'OLD', datetime(2026, 1, 1)),
            _row(_FT, 'GC02M1-3.5', 'BIN2', '10', 'NEW', datetime(2026, 1, 2)),
            _row(_FT, 'GC02M1-3.5', 'FAIL', '11', 'KEEP', None),
        ]
        batches, ignored = group_source(rows, _IGNORE, _line_of)
        self.assertEqual(ignored, 0)
        self.assertEqual(len(batches), 1)
        batch = batches[0]
        self.assertEqual(batch.line, 'FT')
        self.assertEqual(batch.conflicts, 1)
        self.assertEqual([(item.code, item.grade, item.name) for item in batch.winners], [
            (10, 'BIN2', 'NEW'),
            (11, 'FAIL', 'KEEP'),
        ])

        existing = [
            ExistingDefect(id=2, code=10, grade='FAIL', name='OLD', bsl=None),
            ExistingDefect(id=1, code=10, grade='FAIL', name='OLD', bsl=0.2),
            ExistingDefect(id=3, code=99, grade='FAIL', name='GONE', bsl=0.1),
        ]
        plan = plan_changes(existing, batch.winners)
        self.assertEqual(plan.updates, [(1, 'BIN2', 'NEW')])
        self.assertCountEqual(plan.delete_ids, [2, 3])
        self.assertEqual([(item.code, item.name) for item in plan.inserts], [(11, 'KEEP')])

    def test_negative_code_and_invalid_code(self):
        rows = [
            _row(_WLT, 'GC02M1-2.6', 'FAIL', '-998', 'TCP', datetime(2026, 2, 1)),
            _row(_WLT, 'GC02M1-2.6', 'FAIL', 'ABC', 'BAD', datetime(2026, 2, 1)),
        ]
        batches, _ignored = group_source(rows, _IGNORE, _line_of)
        self.assertEqual(batches[0].line, 'WLT')
        self.assertEqual(batches[0].invalid, 1)
        self.assertEqual(batches[0].winners, [Winner(code=-998, grade='FAIL', name='TCP')])

    def test_ignore_wlt_gc3003_does_not_replace_ft(self):
        rows = [
            _row(_FT, 'GC3003-3.5', 'FAIL', '10', 'FROM_FT', datetime(2026, 1, 1)),
            _row(_WLT, 'GC3003-3.5', 'BIN2', '20', 'FROM_WLT', datetime(2026, 6, 1)),
        ]
        batches, ignored = group_source(rows, _IGNORE, _line_of)
        self.assertEqual(ignored, 1)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].line, 'FT')
        self.assertEqual(batches[0].winners, [Winner(code=10, grade='FAIL', name='FROM_FT')])

        applied = []

        def apply(_pid, plan):
            applied.append(plan)

        logs = []
        run_product_sync(
            batches,
            ignored,
            find_product=lambda _code: 7,
            load_defects=lambda _pid: [
                ExistingDefect(id=5, code=20, grade='BIN2', name='FROM_WLT', bsl=0.4),
            ],
            apply_changes=apply,
            save_log=logs.append,
            run_at=datetime(2026, 10, 8, 2, 0, 0),
        )
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].inserts, [Winner(code=10, grade='FAIL', name='FROM_FT')])
        self.assertEqual(applied[0].delete_ids, [5])
        self.assertEqual(logs[0]['status'], STATUS_OK)
        self.assertIn('已忽略 WLT 分类下的同名配置（1 行）', logs[0]['message'])


class RunSyncTest(unittest.TestCase):
    def test_unregistered_product_does_not_write_defects(self):
        batches, ignored = group_source(
            [_row(_FT, 'NO-SUCH-3.5', 'FAIL', '1', 'X', datetime(2026, 1, 1))],
            _IGNORE,
            _line_of,
        )
        apply = MagicMock()
        logs = []
        run_product_sync(
            batches,
            ignored,
            find_product=lambda _code: None,
            load_defects=MagicMock(),
            apply_changes=apply,
            save_log=logs.append,
            run_at=datetime(2026, 10, 8, 2, 0, 0),
        )
        apply.assert_not_called()
        self.assertEqual(logs[0]['status'], STATUS_SKIPPED)
        self.assertIn('未建档', logs[0]['message'])
        self.assertEqual(logs[0]['inserted'], 0)

    def test_one_model_failure_does_not_stop_the_next(self):
        batches, ignored = group_source([
            _row(_FT, 'A-3.5', 'FAIL', '1', 'A', datetime(2026, 1, 1)),
            _row(_WLT, 'B-2.6', 'FAIL', '2', 'B', datetime(2026, 1, 1)),
        ], _IGNORE, _line_of)
        applied = []

        def find_product(code):
            return 1 if code == 'A-3.5' else 2

        def apply(pid, plan):
            if pid == 1:
                raise RuntimeError('写入失败')
            applied.append((pid, [item.code for item in plan.inserts]))

        logs = []
        run_product_sync(
            batches,
            ignored,
            find_product=find_product,
            load_defects=lambda _pid: [],
            apply_changes=apply,
            save_log=logs.append,
            run_at=datetime(2026, 10, 8, 2, 0, 0),
        )
        self.assertEqual(applied, [(2, [2])])
        self.assertEqual([row['status'] for row in logs], [STATUS_FAILED, STATUS_OK])
        self.assertIn('写入失败', logs[0]['message'])
        self.assertEqual(logs[1]['inserted'], 1)
        self.assertIsInstance(logs[0], dict)
        self.assertEqual(logs[1]['prod_line'], 'WLT')


class SyncLogQueryTest(unittest.TestCase):
    def test_rejects_bad_line(self):
        ok, msg, data = query_sync_logs(prod_line='CP')
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertIn('产线', msg)

    @patch('app.controllers.defect_code_sync_ctrl.DefectCodeSyncLog')
    def test_filters_and_orders(self, model):
        query = model.query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value = query
        row = MagicMock()
        row.to_dict.return_value = {'product_code': 'GC02M1-3.5', 'status': 'OK'}
        query.all.return_value = [row]
        ok, msg, data = query_sync_logs('gc02', 'FT', 'OK')
        self.assertTrue(ok)
        self.assertEqual(data['total'], 1)
        self.assertEqual(query.filter.call_count, 3)
        query.order_by.assert_called_once()
        query.limit.assert_called_once_with(200)


def _page_app():
    app = Flask(__name__, template_folder=str(_APP_DIR / 'templates'))
    app.secret_key = 'defect-sync-test'
    app.config['TESTING'] = True
    app.register_blueprint(auth_bp)
    app.register_blueprint(defect_sync_bp)
    return app


class SyncLogPageTest(unittest.TestCase):
    def test_root_page_has_filters(self):
        app = _page_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 0
            sess['user_name'] = '管理员'
            sess['must_change_password'] = False
        resp = client.get('/admin/defect-sync')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('缺陷同步记录', html)
        self.assertIn('全部产线', html)
        self.assertIn('未建档', html)
        self.assertIn('/admin/defect-sync/api', html)

    def test_api_passes_filters(self):
        app = _page_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 0
            sess['must_change_password'] = False
        with patch(
            'app.routes.defect_code_sync_routes.query_sync_logs',
            return_value=(True, '获取成功', {'rows': [], 'total': 0}),
        ) as query:
            resp = client.get('/admin/defect-sync/api?product_code=GC3003-3.5&prod_line=FT&status=OK')
        self.assertEqual(resp.status_code, 200)
        query.assert_called_once_with(
            product_code='GC3003-3.5',
            prod_line='FT',
            status='OK',
        )

    def test_engineer_cannot_open_page(self):
        app = _page_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 2
            sess['role'] = 1
            sess['must_change_password'] = False
        resp = client.get('/admin/defect-sync')
        self.assertEqual(resp.status_code, 302)


class SourceFetchTest(unittest.TestCase):
    def test_source_is_one_query_in_one_batch(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value = cursor
        self.assertEqual(fetch_source_rows(conn), [])
        conn.cursor.assert_called_once()
        cursor.execute.assert_called_once()
        self.assertGreaterEqual(cursor.arraysize, _SOURCE_ARRAYSIZE)
        self.assertGreaterEqual(cursor.prefetchrows, _SOURCE_ARRAYSIZE)
        sql = cursor.execute.call_args.args[0]
        self.assertEqual(sql.count('SELECT'), 1)
        self.assertNotIn('CONFIG_KEY1 =', sql)


class PlanShapeTest(unittest.TestCase):
    def test_unchanged_row_is_not_an_update(self):
        plan = plan_changes(
            [ExistingDefect(id=8, code=3, grade='FAIL', name='SAME', bsl=1.5)],
            [Winner(code=3, grade='FAIL', name='SAME')],
        )
        self.assertEqual(plan, ChangePlan())


if __name__ == '__main__':
    unittest.main()
