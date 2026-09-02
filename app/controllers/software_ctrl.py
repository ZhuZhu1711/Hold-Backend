"""root 维护 SOFTWARE_INFO（客户端版本 + 发布说明）。"""
from __future__ import annotations

from app.controllers.common_data_ctrl import (
    get_latest_software_info,
    update_latest_software_info,
)
from app.utils.git_changelog import (
    COMMENT_MAX_BYTES,
    DEFAULT_MAX_COUNT,
    VERSION_MAX_LEN,
    build_changelog_draft,
    read_client_app_version,
    suggest_next_version,
)


def get_software_admin_payload():
    ok, msg, data = get_latest_software_info()
    if not ok:
        return ok, msg, None
    version = str((data or {}).get('version') or '').strip()
    comment = str((data or {}).get('comment') or '').strip()
    code_version = read_client_app_version()
    return True, 'success', {
        'version': version,
        'comment': comment,
        'suggested_version': suggest_next_version(version) if version else (
            code_version or '1.0.0'
        ),
        'client_code_version': code_version,
        'version_max_len': VERSION_MAX_LEN,
        'comment_max_bytes': COMMENT_MAX_BYTES,
    }


def save_software_admin(version, comment):
    return update_latest_software_info(version, comment)


def get_software_changelog(
    *,
    include_client: bool = True,
    include_backend: bool = False,
    max_count: int = DEFAULT_MAX_COUNT,
):
    if not include_client and not include_backend:
        include_client = True
    payload = build_changelog_draft(
        include_client=include_client,
        include_backend=include_backend,
        max_count=max_count,
    )
    if payload.get('available'):
        return True, 'success', payload
    hint = str(payload.get('hint') or '').strip() or '无法读取 git 提交历史，请手动填写发布说明'
    return True, hint, payload
