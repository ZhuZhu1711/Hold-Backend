"""手提 / AQL Hold 附件路径：`ANNEX_FTP_PATH` 以 `@path` 拼接。"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime

from app.config import Config
from app.utils.FtpPool import FtpUnavailableError, testlog_ftp_pool

HOLD_CODE_AQL = 'AQL_HOLD'
SOURCE_MES = 0
SOURCE_MANUAL = 1
RECORD_TYPE_FT = 0
RECORD_TYPE_WLT = 2
# 手提可选码：后续新增时同步扩这里，并视需要加入合批 _FT_HOLD_CODES / 分析例外。
FT_MANUAL_HOLD_CODES = frozenset({HOLD_CODE_AQL})
WLT_MANUAL_HOLD_CODES = frozenset({'004', '022'})
ANNEX_ALLOWED_EXT = frozenset({'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'})
ANNEX_MAX_FILES = 20
ANNEX_MAX_BYTES = 10 * 1024 * 1024

_MIME_BY_EXT = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.gif': 'image/gif',
    '.bmp': 'image/bmp',
    '.webp': 'image/webp',
}


def hold_code_is_aql(hold_code) -> bool:
    text = str(hold_code or '').strip()
    if not text:
        return False
    tokens = [p.strip().upper() for p in re.split(r'[@,;]+', text) if p.strip()]
    return HOLD_CODE_AQL in tokens


def parse_annex_ftp_paths(raw) -> list:
    text = str(raw or '').strip()
    if not text:
        return []
    return [p.strip() for p in text.split('@') if p.strip()]


def join_annex_ftp_paths(paths) -> str | None:
    cleaned = []
    seen = set()
    for raw in paths or []:
        s = str(raw or '').strip().lstrip('@').strip()
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    if not cleaned:
        return None
    return ''.join('@' + p for p in cleaned)


def annex_mimetype(ftp_path: str) -> str:
    ext = os.path.splitext(str(ftp_path or ''))[1].lower()
    return _MIME_BY_EXT.get(ext, 'application/octet-stream')


def annex_root_dir() -> str:
    root = str(getattr(Config, 'ANNEX_FTP_REMOTE_DIR', None) or '/JDY_UPLOAD/HOLD_ANNEX/').strip()
    if not root.startswith('/'):
        root = '/' + root
    return root.rstrip('/') + '/'


def _safe_seg(text, fallback='x') -> str:
    s = re.sub(r'[^A-Za-z0-9._-]+', '_', str(text or '').strip())
    return (s[:60] if s else fallback)


def _ftp_makedirs(ftp, directory: str) -> None:
    parts = [p for p in str(directory or '').replace('\\', '/').strip('/').split('/') if p]
    curr = ''
    for part in parts:
        curr += '/' + part
        try:
            ftp.mkd(curr)
        except Exception:
            pass


def upload_annex_files(files, product_id='', lot_id='') -> list:
    """
    files: iterable of (filename, bytes)
    返回已上传的远程绝对路径列表。
    """
    items = []
    for name, payload in files or []:
        fname = os.path.basename(str(name or '').strip()) or 'image'
        ext = os.path.splitext(fname)[1].lower()
        if ext not in ANNEX_ALLOWED_EXT:
            raise ValueError(f'不支持的图片格式: {fname}')
        data = payload if isinstance(payload, (bytes, bytearray)) else bytes(payload or b'')
        if not data:
            raise ValueError(f'图片为空: {fname}')
        if len(data) > ANNEX_MAX_BYTES:
            raise ValueError(f'图片过大（上限 10MB）: {fname}')
        items.append((fname, ext, bytes(data)))
    if not items:
        return []
    if len(items) > ANNEX_MAX_FILES:
        raise ValueError(f'图片最多 {ANNEX_MAX_FILES} 张')

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest_dir = (
        annex_root_dir()
        + _safe_seg(product_id, 'product')
        + '/'
        + _safe_seg(lot_id, 'lot')
    )
    ftp = None
    remote_paths = []
    try:
        ftp = testlog_ftp_pool.get_conn()
        _ftp_makedirs(ftp, dest_dir)
        for i, (fname, ext, data) in enumerate(items, start=1):
            remote = f'{dest_dir}/{stamp}_{i}{ext}'
            ftp.storbinary(f'STOR {remote}', io.BytesIO(data))
            remote_paths.append(remote)
        return remote_paths
    except FtpUnavailableError as e:
        raise ValueError(f'FTP 不可用，无法上传附件: {e}') from e
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f'上传附件失败: {e}') from e
    finally:
        if ftp is not None:
            testlog_ftp_pool.return_conn(ftp)


def download_annex_bytes(ftp_path: str) -> bytes:
    path = str(ftp_path or '').strip()
    if not path:
        raise ValueError('附件路径为空')
    ftp = None
    buf = io.BytesIO()
    try:
        ftp = testlog_ftp_pool.get_conn()
        ftp.retrbinary(f'RETR {path}', buf.write)
        data = buf.getvalue()
        if not data:
            raise ValueError('附件为空')
        return data
    except FtpUnavailableError as e:
        raise ValueError(f'FTP 不可用，无法下载附件: {e}') from e
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f'下载附件失败: {e}') from e
    finally:
        if ftp is not None:
            testlog_ftp_pool.return_conn(ftp)
