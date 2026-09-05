"""版本说明：一版本一份 Markdown，发布新版本不覆盖旧文件。"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from app.config import argv_is_debug_mode
from app.utils.markdown_render import markdown_to_html

MAX_BYTES = 512 * 1024
ALLOWED_EXT = {'.md', '.markdown'}
META_VERSION = 1
VERSION_RE = re.compile(r'^v?(\d+(?:\.\d+){1,4})$', re.I)
VERSION_SORT_RE = re.compile(r'\d+')


def default_data_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(os.path.dirname(os.path.abspath(sys.executable))) / 'data'
    return Path(__file__).resolve().parents[2] / 'data'


def _data_dir(base_dir=None) -> Path:
    if base_dir is not None:
        return Path(base_dir)
    override = (os.environ.get('HOLD_RELEASE_NOTES_DIR') or '').strip()
    if override:
        return Path(override)
    try:
        from flask import current_app

        configured = current_app.config.get('HOLD_RELEASE_NOTES_DIR')
        if configured:
            return Path(configured)
    except RuntimeError:
        pass
    return default_data_dir()


def _folder_name() -> str:
    return 'release_notes_test' if argv_is_debug_mode() else 'release_notes'


def notes_dir(base_dir=None) -> Path:
    return _data_dir(base_dir) / _folder_name()


def parse_version(raw) -> str:
    text = str(raw or '').strip()
    matched = VERSION_RE.fullmatch(text)
    if not matched:
        return ''
    return matched.group(1)


def _version_from_filename(name) -> str:
    text = str(name or '').replace('\\', '/').strip()
    text = os.path.basename(text)
    stem, ext = os.path.splitext(text)
    if ext.lower() not in ALLOWED_EXT:
        return ''
    return parse_version(stem)


def _version_key(version: str) -> tuple:
    nums = [int(tok) for tok in VERSION_SORT_RE.findall(version)]
    return tuple(nums) if nums else (0,)


def _read_meta(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _item_from_files(version: str, md_file: Path, meta_file: Path) -> dict | None:
    try:
        markdown = md_file.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeDecodeError):
        return None
    meta = _read_meta(meta_file)
    return {
        'version': version,
        'markdown': markdown,
        'html': markdown_to_html(markdown),
        'filename': str(meta.get('filename') or md_file.name),
        'uploaded_by': str(meta.get('uploaded_by') or ''),
        'uploaded_at': str(meta.get('uploaded_at') or ''),
        'size': int(meta.get('size') or md_file.stat().st_size),
    }


def _migrate_legacy(base_dir=None) -> None:
    """把旧的单文件说明迁到按版本目录，避免已发布内容丢失。"""
    folder = notes_dir(base_dir)
    if any(folder.glob('*.md')):
        return
    root = _data_dir(base_dir)
    for stem in ('release_notes_test', 'release_notes'):
        old_md = root / f'{stem}.md'
        if not old_md.is_file():
            continue
        meta = _read_meta(root / f'{stem}.meta.json')
        version = parse_version(meta.get('version')) or _version_from_filename(
            meta.get('filename')
        )
        if not version:
            continue
        try:
            text = old_md.read_text(encoding='utf-8-sig')
        except (OSError, UnicodeDecodeError):
            continue
        info = {
            'v': META_VERSION,
            'version': version,
            'filename': str(meta.get('filename') or old_md.name),
            'uploaded_by': str(meta.get('uploaded_by') or ''),
            'uploaded_at': str(meta.get('uploaded_at') or ''),
            'size': int(meta.get('size') or old_md.stat().st_size),
        }
        try:
            _atomic_write(folder / f'{version}.md', text)
            _atomic_write(
                folder / f'{version}.meta.json',
                json.dumps(info, ensure_ascii=False, indent=2),
            )
        except OSError:
            return


def list_release_notes(base_dir=None) -> tuple[bool, str, dict]:
    try:
        _migrate_legacy(base_dir)
    except OSError:
        pass
    folder = notes_dir(base_dir)
    items = []
    if folder.is_dir():
        for md_file in folder.glob('*.md'):
            version = parse_version(md_file.stem)
            if not version:
                continue
            item = _item_from_files(version, md_file, folder / f'{version}.meta.json')
            if item:
                items.append(item)
    items.sort(key=lambda row: (_version_key(row['version']), row['uploaded_at']), reverse=True)
    return True, 'success', {'items': items}


def get_release_notes(base_dir=None) -> tuple[bool, str, dict]:
    return list_release_notes(base_dir)


def _decode_markdown(raw: bytes) -> tuple[str | None, str]:
    data = raw if isinstance(raw, (bytes, bytearray)) else b''
    if not data:
        return None, '请上传 Markdown 文件'
    if len(data) > MAX_BYTES:
        return None, f'文件不能超过 {MAX_BYTES // 1024} KB'
    if b'\x00' in data:
        return None, '不支持二进制文件'
    try:
        text = bytes(data).decode('utf-8-sig')
    except UnicodeDecodeError:
        return None, '请上传 UTF-8 编码的 Markdown'
    if not text.strip():
        return None, 'Markdown 内容为空'
    return text, ''


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(content, encoding='utf-8')
    os.replace(tmp, path)


def save_release_notes(
    filename,
    raw,
    operator='',
    version='',
    base_dir=None,
) -> tuple[bool, str, dict]:
    ext = os.path.splitext(os.path.basename(str(filename or '').replace('\\', '/')))[1].lower()
    if ext not in ALLOWED_EXT:
        return False, '请上传 .md / .markdown 文件', None
    resolved = parse_version(version) or _version_from_filename(filename)
    if not resolved:
        return False, '请填写版本号，或把文件命名为 2.0.9.md', None

    text, err = _decode_markdown(raw)
    if err:
        return False, err, None

    folder = notes_dir(base_dir)
    md_file = folder / f'{resolved}.md'
    existed = md_file.is_file()
    original = os.path.basename(str(filename or '').replace('\\', '/')) or f'{resolved}.md'
    info = {
        'v': META_VERSION,
        'version': resolved,
        'filename': original,
        'uploaded_by': str(operator or '').strip(),
        'uploaded_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'size': len(text.encode('utf-8')),
    }
    try:
        _atomic_write(md_file, text)
        _atomic_write(folder / f'{resolved}.meta.json', json.dumps(info, ensure_ascii=False, indent=2))
    except OSError:
        return False, '保存版本说明失败', None

    ok, msg, data = list_release_notes(base_dir)
    if not ok:
        return ok, msg, data
    hint = f'已更新 {resolved}' if existed else f'已发布 {resolved}'
    return True, hint, data
