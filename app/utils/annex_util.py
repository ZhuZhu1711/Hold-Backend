"""手提 / AQL Hold 附件路径：`ANNEX_FTP_PATH` 以 `@path` 拼接。"""
from __future__ import annotations

import io
import os
import re

from app.config import Config
from app.utils.FtpPool import FtpUnavailableError, annex_ftp_pool

HOLD_CODE_AQL = 'AQL_HOLD'
SOURCE_MES = 0
SOURCE_MANUAL = 1
RECORD_TYPE_FT = 0
RECORD_TYPE_WLT = 2
# 手提可选码：后续新增时同步扩这里，并视需要加入合批 _FT_HOLD_CODES / 分析例外。
FT_MANUAL_HOLD_CODES = frozenset({HOLD_CODE_AQL})
WLT_MANUAL_HOLD_CODES = frozenset({'004', '022'})
WLT_MANUAL_STATION = 'WLT2'
_FVI_STATIONS = frozenset({'FAOIFINISH', 'FFVI'})
WLT_WAFER_MIN = 1
WLT_WAFER_MAX = 25
ANNEX_ALLOWED_EXT = frozenset({'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'})
ANNEX_MAX_FILES = 25
ANNEX_MAX_BYTES = 10 * 1024 * 1024
ANNEX_FTP_PATH_MAX = 1024

_MIME_BY_EXT = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.gif': 'image/gif',
    '.bmp': 'image/bmp',
    '.webp': 'image/webp',
}


def ft_manual_stations() -> tuple:
    """FT 手提可选站点：合批站点去掉 FVI。"""
    stations = getattr(Config, 'HOLD_MERGE_STATIONS', None) or []
    return tuple(s for s in stations if s and str(s).strip() not in _FVI_STATIONS)


def product_suffix_for_line(line: str) -> str:
    return '-3.5' if str(line or '').upper() == 'FT' else '-2.6'


def parse_wlt_wafer_nos(wafer_id=None, extra=None) -> list:
    """解析 1~25 片号，去重升序。"""
    nos = []
    for raw in extra or []:
        try:
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if WLT_WAFER_MIN <= n <= WLT_WAFER_MAX:
            nos.append(n)
    text = str(wafer_id or '').strip()
    if text:
        found = [int(m) for m in re.findall(r'#(\d{1,2})', text)]
        if not found and '#' not in text:
            for part in re.split(r'[,;\s]+', text):
                if part.isdigit():
                    found.append(int(part))
        for n in found:
            if WLT_WAFER_MIN <= n <= WLT_WAFER_MAX:
                nos.append(n)
    return sorted(set(nos))


def format_wlt_wafer_id(nos) -> str:
    return ''.join(f'#{int(n):02d}' for n in parse_wlt_wafer_nos(extra=nos))


def wlt_lot_prefix(lot_id) -> str:
    text = str(lot_id or '').strip()
    if not text:
        return ''
    m = re.match(r'^(.*?)[.-](\d{1,2})$', text)
    return (m.group(1).strip() if m else text)


def format_wlt_lot_id(lot_id, first_no) -> str:
    """
    写入 LOT.NO。已带 .NN / -NN 时保留用户填写的 NO（本 lot 第一片）；
    否则用所选片中的最小片号补上。
    """
    text = str(lot_id or '').strip()
    if not text:
        return ''
    m = re.match(r'^(.*)[.-](\d{1,2})$', text)
    if m and m.group(1).strip():
        return f'{m.group(1).strip()}.{int(m.group(2)):02d}'
    return f'{text}.{int(first_no):02d}'


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


def annex_line_from_record(record) -> str:
    if not isinstance(record, dict):
        return 'FT'
    try:
        if int(record.get('RECORD_TYPE')) == RECORD_TYPE_WLT:
            return 'WLT'
    except (TypeError, ValueError):
        pass
    pid = str(record.get('PRODUCT_ID') or '')
    return 'WLT' if pid.endswith('-2.6') else 'FT'


def resolve_annex_ftp_path(stored, line='') -> str:
    """相对名（如 12_1.jpg）补上产线根目录；已是绝对路径则原样。"""
    path = str(stored or '').strip().lstrip('@').strip()
    if not path:
        return ''
    if path.startswith('/'):
        return path
    return annex_root_dir(line) + path.lstrip('/')


def annex_root_dir(line='') -> str:
    is_wlt = str(line or '').upper() == 'WLT'
    key = 'ANNEX_FTP_WLT_DIR' if is_wlt else 'ANNEX_FTP_FT_DIR'
    default = '/JDY_UPLOAD/WLT_MANUAL/' if is_wlt else '/JDY_UPLOAD/FT_MANUAL/'
    root = str(getattr(Config, key, None) or default).strip()
    if not root.startswith('/'):
        root = '/' + root
    return root.rstrip('/') + '/'


def _ftp_makedirs(ftp, directory: str) -> None:
    parts = [p for p in str(directory or '').replace('\\', '/').strip('/').split('/') if p]
    curr = ''
    for part in parts:
        curr += '/' + part
        try:
            ftp.mkd(curr)
        except Exception:
            pass


def _ftp_cwd(ftp, directory: str) -> None:
    """进入远程目录；兼容带/不带前导斜杠。"""
    raw = str(directory or '/').replace('\\', '/').strip() or '/'
    if not raw.startswith('/'):
        raw = '/' + raw
    raw = raw.rstrip('/') or '/'
    last_err = None
    for cand in (raw, raw + '/', raw.lstrip('/') or '/'):
        try:
            ftp.cwd(cand)
            return
        except Exception as e:
            last_err = e
    raise ValueError(f'无法进入目录 {raw}: {last_err}')


def _ftp_reset_home(ftp) -> None:
    try:
        ftp.cwd('/')
    except Exception:
        pass


def upload_annex_files(files, line='', record_id=None) -> list:
    """
    files: iterable of (filename, bytes)
    平铺上传到 FT_MANUAL / WLT_MANUAL，文件名 {recordId}_{n}.ext。
    返回相对文件名列表（写入 ANNEX_FTP_PATH，下载时再拼根目录），以控制 1024 长度。
    """
    try:
        rid = int(record_id)
    except (TypeError, ValueError):
        raise ValueError('缺少 record id，无法命名附件')
    if rid <= 0:
        raise ValueError('record id 无效')

    items = []
    for name, payload in files or []:
        fname = os.path.basename(str(name or '').strip()) or 'image'
        ext = os.path.splitext(fname)[1].lower()
        if ext not in ANNEX_ALLOWED_EXT:
            raise ValueError(f'不支持的图片格式: {fname}')
        if ext == '.jpeg':
            ext = '.jpg'
        data = payload if isinstance(payload, (bytes, bytearray)) else bytes(payload or b'')
        if not data:
            raise ValueError(f'图片为空: {fname}')
        if len(data) > ANNEX_MAX_BYTES:
            raise ValueError(f'图片过大（上限 10MB）: {fname}')
        items.append((ext, bytes(data)))
    if not items:
        return []
    if len(items) > ANNEX_MAX_FILES:
        raise ValueError(f'图片最多 {ANNEX_MAX_FILES} 张')

    dest_dir = annex_root_dir(line).rstrip('/')
    ftp = None
    stored_names = []
    try:
        ftp = annex_ftp_pool.get_conn()
        _ftp_makedirs(ftp, dest_dir)
        _ftp_cwd(ftp, dest_dir)
        for i, (ext, data) in enumerate(items, start=1):
            stored = f'{rid}_{i}{ext}'
            ftp.storbinary(f'STOR {stored}', io.BytesIO(data))
            stored_names.append(stored)
        return stored_names
    except FtpUnavailableError as e:
        raise ValueError(f'FTP 不可用，无法上传附件: {e}') from e
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f'上传附件失败: {e}') from e
    finally:
        if ftp is not None:
            _ftp_reset_home(ftp)
            annex_ftp_pool.return_conn(ftp)


def download_annex_bytes(ftp_path: str, line='') -> bytes:
    path = resolve_annex_ftp_path(ftp_path, line)
    if not path:
        raise ValueError('附件路径为空')
    ftp = None
    try:
        ftp = annex_ftp_pool.get_conn()
        data = _retr_annex_bytes(ftp, path)
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
            _ftp_reset_home(ftp)
            annex_ftp_pool.return_conn(ftp)


def _retr_annex_bytes(ftp, path: str) -> bytes:
    """先 cwd 到目录再按文件名下载；失败再试绝对路径。"""
    text = str(path or '').replace('\\', '/').strip()
    directory, _, name = text.rpartition('/')
    errors = []
    if name:
        try:
            if directory:
                _ftp_cwd(ftp, directory)
            buf = io.BytesIO()
            ftp.retrbinary(f'RETR {name}', buf.write)
            data = buf.getvalue()
            if data:
                return data
        except Exception as e:
            errors.append(str(e))
            _ftp_reset_home(ftp)
    for cand in (text, text.lstrip('/'), '/' + text.lstrip('/')):
        if not cand:
            continue
        try:
            buf = io.BytesIO()
            ftp.retrbinary(f'RETR {cand}', buf.write)
            data = buf.getvalue()
            if data:
                return data
        except Exception as e:
            errors.append(str(e))
    raise ValueError('下载附件失败: ' + (errors[-1] if errors else '未知错误'))
