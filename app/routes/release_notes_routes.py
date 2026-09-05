from flask import Blueprint, jsonify, render_template, request, session

from app.controllers import release_notes_ctrl
from app.utils.auth_decorators import (
    current_role_name,
    is_root,
    login_required,
    root_required,
    ROLE_ENGINEER,
    ROLE_PRODUCTION,
    ROLE_QUALITY,
)

release_notes_bp = Blueprint('release_notes', __name__, url_prefix='/release-notes')


def _nav_area():
    role = session.get('role')
    if role == ROLE_ENGINEER:
        return 'eng'
    if role == ROLE_PRODUCTION:
        return 'prod'
    if role == ROLE_QUALITY:
        return 'qa'
    return 'admin'


@release_notes_bp.route('')
@login_required
def release_notes_page():
    return render_template(
        'release_notes.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
        nav_area=_nav_area(),
        can_upload=is_root(),
    )


@release_notes_bp.route('/api', methods=['GET'])
@login_required
def get_release_notes():
    success, msg, data = release_notes_ctrl.get_release_notes()
    if not success:
        return jsonify({'code': 500, 'msg': msg, 'data': None}), 500
    payload = dict(data or {})
    payload['can_upload'] = is_root()
    return jsonify({'code': 200, 'msg': msg, 'data': payload})


@release_notes_bp.route('/api', methods=['POST'])
@root_required
def upload_release_notes():
    overhead = 8192
    max_len = release_notes_ctrl.MAX_BYTES + overhead
    if request.content_length is not None and request.content_length > max_len:
        return jsonify({'code': 413, 'msg': '文件不能超过 512 KB', 'data': None}), 413
    upload = request.files.get('file') or request.files.get('markdown')
    if upload is None:
        return jsonify({'code': 400, 'msg': '请选择 Markdown 文件', 'data': None}), 400
    raw = upload.stream.read(release_notes_ctrl.MAX_BYTES + 1)
    if len(raw) > release_notes_ctrl.MAX_BYTES:
        return jsonify({'code': 400, 'msg': '文件不能超过 512 KB', 'data': None}), 400
    success, msg, data = release_notes_ctrl.save_release_notes(
        upload.filename,
        raw,
        operator=session.get('user_name') or '',
        version=request.form.get('version') or '',
    )
    if not success:
        status = 400 if '请' in (msg or '') or '不能超过' in (msg or '') or '空' in (msg or '') else 500
        return jsonify({'code': status, 'msg': msg, 'data': None}), status
    payload = dict(data or {})
    payload['can_upload'] = True
    return jsonify({'code': 200, 'msg': msg, 'data': payload})
