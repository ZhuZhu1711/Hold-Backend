"""git 发布说明草稿（不要求真实 git 仓库）。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.utils.git_changelog import (
    clip_oracle_varchar,
    format_commit_lines,
    read_client_app_version,
    suggest_next_version,
    _parse_log,
)


class SuggestNextVersionTest(unittest.TestCase):
    def test_bumps_patch(self):
        self.assertEqual(suggest_next_version('2.0.7'), '2.0.8')

    def test_empty_defaults(self):
        self.assertEqual(suggest_next_version(''), '1.0.0')
        self.assertEqual(suggest_next_version(None), '1.0.0')


class ParseLogTest(unittest.TestCase):
    def test_skips_merge_and_duplicates(self):
        stdout = '\n'.join([
            'abc\t2026-09-02\t修复合批 Hold 次数',
            'def\t2026-09-01\tMerge pull request #12 from x/y',
            'ghi\t2026-08-31\t修复合批 Hold 次数',
            'jkl\t2026-08-30\t支持系统托盘',
        ])
        commits = _parse_log(stdout)
        self.assertEqual(
            [c['subject'] for c in commits],
            ['修复合批 Hold 次数', '支持系统托盘'],
        )

    def test_format_bullets(self):
        text = format_commit_lines([
            {'subject': '修复 A'},
            {'subject': '增加 B'},
        ])
        self.assertEqual(text, '- 修复 A\n- 增加 B')


class ClipAndVersionFileTest(unittest.TestCase):
    def test_clip_utf8_bytes(self):
        text = '测' * 10
        clipped = clip_oracle_varchar(text, max_bytes=8)
        self.assertLessEqual(len(clipped.encode('utf-8')), 8)
        self.assertTrue(clipped.startswith('测'))

    def test_read_app_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            target = repo / 'hold_client'
            target.mkdir()
            (target / 'version.py').write_text(
                'APP_VERSION = "2.0.9"\n',
                encoding='utf-8',
            )
            self.assertEqual(read_client_app_version(repo), '2.0.9')

    def test_read_app_version_missing(self):
        self.assertEqual(read_client_app_version(Path('/no/such/repo')), '')


class CollectChangelogMissingGitTest(unittest.TestCase):
    @patch('app.utils.git_changelog._git_exe', return_value=None)
    def test_missing_git(self, _git):
        from app.utils.git_changelog import collect_repo_changelog

        payload = collect_repo_changelog(Path('.'))
        self.assertFalse(payload['available'])
        self.assertIn('git', payload['hint'].lower())


if __name__ == '__main__':
    unittest.main()
