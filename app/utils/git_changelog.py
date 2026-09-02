"""从本地 git 仓库生成发布说明草稿（失败则返回空，由页面手填）。"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

APP_VERSION_RE = re.compile(
    r'^APP_VERSION\s*=\s*[\'"]([^\'"]+)[\'"]',
    re.MULTILINE,
)
VERSION_NUM_RE = re.compile(r'\d+')
_SKIP_SUBJECT_RE = re.compile(
    r'^(merge( pull request)?\b|wip\b)',
    re.IGNORECASE,
)

GIT_TIMEOUT_SEC = 8
DEFAULT_MAX_COUNT = 12
MAX_COUNT_LIMIT = 40
COMMENT_MAX_BYTES = 2048
VERSION_MAX_LEN = 100


def backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def client_repo_path() -> Path:
    return backend_root().parent / 'FT-HoldSys'


def backend_repo_path() -> Path:
    return backend_root()


def suggest_next_version(current: str | None) -> str:
    text = str(current or '').strip()
    nums = [int(tok) for tok in VERSION_NUM_RE.findall(text)]
    if not nums:
        return '1.0.0'
    nums[-1] += 1
    return '.'.join(str(n) for n in nums)


def read_client_app_version(repo: Path | None = None) -> str:
    path = (repo or client_repo_path()) / 'hold_client' / 'version.py'
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return ''
    matched = APP_VERSION_RE.search(text)
    return (matched.group(1).strip() if matched else '')


def clip_oracle_varchar(text: str | None, max_bytes: int = COMMENT_MAX_BYTES) -> str:
    raw = (text or '').strip()
    encoded = raw.encode('utf-8')
    if len(encoded) <= max_bytes:
        return raw
    clipped = encoded[:max_bytes]
    return clipped.decode('utf-8', errors='ignore').rstrip()


def format_commit_lines(commits: list[dict]) -> str:
    lines = []
    for item in commits:
        subject = str(item.get('subject') or '').strip()
        if not subject:
            continue
        lines.append(f'- {subject}')
    return '\n'.join(lines)


def _git_exe() -> str | None:
    from shutil import which

    return which('git')


def _run_git(repo: Path, args: list[str]) -> tuple[bool, str]:
    git = _git_exe()
    if not git:
        return False, '本机未找到 git'
    if not (repo / '.git').exists() and not (repo / '.git').is_file():
        return False, f'不是 git 仓库: {repo}'

    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GIT_OPTIONAL_LOCKS'] = '0'
    kwargs = {
        'args': [
            git,
            '-C',
            str(repo),
            '-c',
            'i18n.logoutputencoding=utf-8',
            *args,
        ],
        'capture_output': True,
        'text': True,
        'timeout': GIT_TIMEOUT_SEC,
        'env': env,
        'encoding': 'utf-8',
        'errors': 'replace',
    }
    if os.name == 'nt':
        kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        completed = subprocess.run(**kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or '').strip()
        return False, err or f'git 退出码 {completed.returncode}'
    return True, completed.stdout or ''


def _parse_log(stdout: str) -> list[dict]:
    commits = []
    seen = set()
    for raw_line in (stdout or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split('\t', 2)
        if len(parts) < 3:
            continue
        sha, date, subject = (parts[0].strip(), parts[1].strip(), parts[2].strip())
        if not subject or _SKIP_SUBJECT_RE.match(subject):
            continue
        key = subject.casefold()
        if key in seen:
            continue
        seen.add(key)
        commits.append({'sha': sha, 'date': date, 'subject': subject})
    return commits


def collect_repo_changelog(
    repo: Path,
    *,
    max_count: int = DEFAULT_MAX_COUNT,
) -> dict:
    limit = max(1, min(int(max_count or DEFAULT_MAX_COUNT), MAX_COUNT_LIMIT))
    ok, output = _run_git(
        repo,
        [
            'log',
            f'--max-count={limit}',
            '--no-merges',
            '--pretty=format:%h%x09%ad%x09%s',
            '--date=short',
        ],
    )
    if not ok:
        return {
            'available': False,
            'path': str(repo),
            'hint': output,
            'commits': [],
            'comment_draft': '',
        }
    commits = _parse_log(output)
    return {
        'available': True,
        'path': str(repo),
        'hint': '',
        'commits': commits,
        'comment_draft': format_commit_lines(commits),
    }


def build_changelog_draft(
    *,
    include_client: bool = True,
    include_backend: bool = False,
    max_count: int = DEFAULT_MAX_COUNT,
) -> dict:
    sections = []
    sources = []
    chosen = []
    if include_client:
        chosen.append(('客户端', client_repo_path()))
    if include_backend:
        chosen.append(('后台', backend_repo_path()))
    for label, repo in chosen:
        payload = collect_repo_changelog(repo, max_count=max_count)
        payload['label'] = label
        sources.append(payload)
        if payload.get('comment_draft'):
            sections.append((label, payload['comment_draft']))

    if len(sections) == 1:
        draft = sections[0][1]
    else:
        draft = '\n\n'.join(f'【{label}】\n{text}' for label, text in sections)
    draft = clip_oracle_varchar(draft)
    available = any(item.get('available') for item in sources)
    hints = [item.get('hint') for item in sources if item.get('hint')]
    return {
        'available': available,
        'comment_draft': draft,
        'hint': '；'.join(hints),
        'sources': sources,
        'client_code_version': read_client_app_version(),
    }
