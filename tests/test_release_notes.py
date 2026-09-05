"""版本说明：Markdown 渲染 + 按版本保留（不连库）。"""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.controllers import release_notes_ctrl
from app.routes.auth_routes import auth_bp
from app.routes.release_notes_routes import release_notes_bp
from app.utils.auth_decorators import ROLE_ENGINEER, ROLE_PRODUCTION, ROLE_QUALITY, ROLE_ROOT
from app.utils.markdown_render import markdown_to_html

_APP_DIR = Path(__file__).resolve().parents[1] / 'app'


def _app():
    app = Flask(__name__, template_folder=str(_APP_DIR / 'templates'))
    app.secret_key = 'test-release-notes'
    app.config['TESTING'] = True
    app.register_blueprint(auth_bp)
    app.register_blueprint(release_notes_bp)
    return app


class MarkdownRenderTest(unittest.TestCase):
    def test_heading_list_and_bold(self):
        html = markdown_to_html('# 标题\n\n- 一项 **加粗**\n- 二项')
        self.assertIn('<h1>标题</h1>', html)
        self.assertIn('<ul>', html)
        self.assertIn('<strong>加粗</strong>', html)

    def test_escapes_raw_html(self):
        html = markdown_to_html('<script>alert(1)</script>\n\n安全')
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_strips_javascript_link(self):
        html = markdown_to_html('[x](javascript:alert(1))')
        self.assertNotIn('javascript:', html.lower())
        self.assertNotIn('href=', html)

    def test_strips_encoded_and_data_links(self):
        for src in (
            '[x](JAVASCRIPT:alert(1))',
            '[x](javascript&#58;alert(1))',
            '[x](data:text/html,hi)',
            '[x](vbscript:alert(1))',
            '[x](//evil.com)',
            '[x](https://ok.com" onclick=alert(1))',
        ):
            html = markdown_to_html(src)
            self.assertNotIn('onclick', html, src)
            self.assertNotIn('javascript:', html.lower(), src)
            self.assertNotIn('data:', html.lower(), src)

    def test_keeps_http_link(self):
        html = markdown_to_html('[文档](https://example.com/a)')
        self.assertIn('href="https://example.com/a"', html)
        self.assertIn('文档', html)

    def test_code_fence_and_table(self):
        src = '```\nprint("<hi>")\n```\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n'
        html = markdown_to_html(src)
        self.assertIn('<pre><code>', html)
        self.assertIn('&lt;hi&gt;', html)
        self.assertIn('<table>', html)
        self.assertIn('<th>A</th>', html)
        self.assertIn('<td>1</td>', html)


class ReleaseNotesStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_when_missing(self):
        ok, msg, data = release_notes_ctrl.get_release_notes(self.base)
        self.assertTrue(ok)
        self.assertEqual(msg, 'success')
        self.assertEqual(data['items'], [])

    def test_upload_keeps_previous_versions(self):
        ok, msg, data = release_notes_ctrl.save_release_notes(
            '2.0.8.md', '# 第一版\n'.encode('utf-8'), operator='root', base_dir=self.base,
        )
        self.assertTrue(ok, msg)
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['version'], '2.0.8')
        self.assertEqual(data['items'][0]['uploaded_by'], 'root')

        ok, msg, data = release_notes_ctrl.save_release_notes(
            'note.md', '# 第二版\n'.encode('utf-8'), operator='管理员',
            version='2.0.9', base_dir=self.base,
        )
        self.assertTrue(ok, msg)
        versions = [row['version'] for row in data['items']]
        self.assertEqual(versions, ['2.0.9', '2.0.8'])
        texts = ' '.join(row['markdown'] for row in data['items'])
        self.assertIn('第一版', texts)
        self.assertIn('第二版', texts)
        folder = release_notes_ctrl.notes_dir(self.base)
        self.assertTrue((folder / '2.0.8.md').is_file())
        self.assertTrue((folder / '2.0.9.md').is_file())

    def test_same_version_updates_only_that_file(self):
        release_notes_ctrl.save_release_notes(
            '2.0.9.md', '# 旧稿\n'.encode('utf-8'), base_dir=self.base,
        )
        release_notes_ctrl.save_release_notes(
            '2.0.8.md', '# 保留\n'.encode('utf-8'), base_dir=self.base,
        )
        ok, msg, data = release_notes_ctrl.save_release_notes(
            '2.0.9.md', '# 新稿\n'.encode('utf-8'), base_dir=self.base,
        )
        self.assertTrue(ok, msg)
        by_ver = {row['version']: row['markdown'] for row in data['items']}
        self.assertIn('新稿', by_ver['2.0.9'])
        self.assertNotIn('旧稿', by_ver['2.0.9'])
        self.assertIn('保留', by_ver['2.0.8'])

    def test_reject_missing_version(self):
        ok, msg, data = release_notes_ctrl.save_release_notes(
            'note.md', b'# hi', base_dir=self.base,
        )
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertIn('版本号', msg)

    def test_reject_non_markdown(self):
        ok, msg, data = release_notes_ctrl.save_release_notes(
            'note.txt', b'# hi', base_dir=self.base,
        )
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertIn('.md', msg)

    def test_reject_empty_and_binary(self):
        ok, msg, _ = release_notes_ctrl.save_release_notes(
            '2.0.0.md', b'   \n', base_dir=self.base,
        )
        self.assertFalse(ok)
        self.assertIn('空', msg)
        ok, msg, _ = release_notes_ctrl.save_release_notes(
            '2.0.0.md', b'abc\x00def', base_dir=self.base,
        )
        self.assertFalse(ok)
        self.assertIn('二进制', msg)

    def test_reject_oversized(self):
        raw = b'# ' + (b'x' * (release_notes_ctrl.MAX_BYTES + 10))
        ok, msg, _ = release_notes_ctrl.save_release_notes('2.0.0.md', raw, base_dir=self.base)
        self.assertFalse(ok)
        self.assertIn('不能超过', msg)

    def test_path_traversal_stays_in_notes_dir(self):
        ok, msg, data = release_notes_ctrl.save_release_notes(
            r'..\..\2.0.9.md', b'# ok', operator='root', base_dir=self.base,
        )
        self.assertTrue(ok, msg)
        folder = release_notes_ctrl.notes_dir(self.base)
        self.assertTrue((folder / '2.0.9.md').is_file())
        self.assertFalse((self.base.parent / '2.0.9.md').exists())

    def test_migrate_legacy_single_file(self):
        (self.base / 'release_notes.md').write_text('# 迁入\n', encoding='utf-8')
        (self.base / 'release_notes.meta.json').write_text(
            '{"filename": "2.0.9.md", "uploaded_by": "root"}',
            encoding='utf-8',
        )
        ok, msg, data = release_notes_ctrl.get_release_notes(self.base)
        self.assertTrue(ok, msg)
        self.assertEqual(data['items'][0]['version'], '2.0.9')
        self.assertIn('迁入', data['items'][0]['markdown'])

    @patch('app.controllers.release_notes_ctrl.argv_is_debug_mode', return_value=True)
    def test_debug_uses_test_folder(self, _mock):
        ok, msg, data = release_notes_ctrl.save_release_notes(
            '2.0.1.md', b'# debug', base_dir=self.base,
        )
        self.assertTrue(ok, msg)
        self.assertTrue((self.base / 'release_notes_test' / '2.0.1.md').is_file())
        self.assertFalse((self.base / 'release_notes' / '2.0.1.md').exists())
        self.assertIn('debug', data['items'][0]['markdown'])


class ReleaseNotesRouteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get('HOLD_RELEASE_NOTES_DIR')
        os.environ['HOLD_RELEASE_NOTES_DIR'] = self._tmp.name
        self.app = _app()
        self.client = self.app.test_client()

    def tearDown(self):
        if self._old is None:
            os.environ.pop('HOLD_RELEASE_NOTES_DIR', None)
        else:
            os.environ['HOLD_RELEASE_NOTES_DIR'] = self._old
        self._tmp.cleanup()

    def _login(self, role, name='用户'):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 9
            sess['user_name'] = name
            sess['role'] = role
            sess['must_change_password'] = False

    def test_anonymous_redirects(self):
        resp = self.client.get('/release-notes')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers.get('Location', ''))
        api = self.client.get('/release-notes/api')
        self.assertEqual(api.status_code, 401)

    def test_all_roles_can_view(self):
        for role in (ROLE_ROOT, ROLE_ENGINEER, ROLE_PRODUCTION, ROLE_QUALITY):
            self._login(role)
            resp = self.client.get('/release-notes')
            self.assertEqual(resp.status_code, 200, role)
            self.assertIn('版本说明'.encode('utf-8'), resp.data)
            api = self.client.get('/release-notes/api')
            self.assertEqual(api.status_code, 200, role)
            body = api.get_json()
            self.assertEqual(body['code'], 200)
            self.assertEqual(body['data']['items'], [])

    def test_root_oversize_body_rejected(self):
        self._login(ROLE_ROOT, name='超管')
        raw = b'# ' + (b'x' * (release_notes_ctrl.MAX_BYTES + 10))
        resp = self.client.post(
            '/release-notes/api',
            data={'file': (io.BytesIO(raw), '2.0.0.md')},
            content_type='multipart/form-data',
        )
        self.assertIn(resp.status_code, (400, 413))
        self.assertIsNone(resp.get_json().get('data'))

    def test_engineer_cannot_upload(self):
        self._login(ROLE_ENGINEER)
        data = {'file': (io.BytesIO(b'# no'), '2.0.0.md')}
        resp = self.client.post('/release-notes/api', data=data, content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 403)

    def test_root_upload_keeps_history(self):
        self._login(ROLE_ROOT, name='超管')
        first = self.client.post(
            '/release-notes/api',
            data={'file': (io.BytesIO('# 旧\n'.encode('utf-8')), '2.0.8.md')},
            content_type='multipart/form-data',
        )
        self.assertEqual(first.status_code, 200, first.get_json())
        second = self.client.post(
            '/release-notes/api',
            data={
                'version': '2.0.9',
                'file': (io.BytesIO('# 新说明\n'.encode('utf-8')), 'notes.md'),
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(second.status_code, 200)
        body = second.get_json()
        versions = [row['version'] for row in body['data']['items']]
        self.assertEqual(versions, ['2.0.9', '2.0.8'])
        texts = ' '.join(row['markdown'] for row in body['data']['items'])
        self.assertIn('新说明', texts)
        self.assertIn('旧', texts)

        self._login(ROLE_PRODUCTION)
        viewed = self.client.get('/release-notes/api').get_json()
        htmls = ' '.join(row['html'] for row in viewed['data']['items'])
        self.assertIn('新说明', htmls)
        self.assertIn('旧', htmls)
        self.assertFalse(viewed['data']['can_upload'])


if __name__ == '__main__':
    unittest.main()
