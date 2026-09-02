from flask import Blueprint, jsonify, render_template, request, session

from app.controllers import software_ctrl
from app.utils.auth_decorators import current_role_name, root_required
from app.utils.git_changelog import DEFAULT_MAX_COUNT

software_bp = Blueprint('software', __name__, url_prefix='/admin/software')


@software_bp.route('')
@root_required
def software_page():
    return render_template(
        'software/edit.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
    )


@software_bp.route('/api', methods=['GET'])
@root_required
def get_software():
    success, msg, data = software_ctrl.get_software_admin_payload()
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    return jsonify({'code': 500, 'msg': msg, 'data': None}), 500


@software_bp.route('/api', methods=['PUT', 'POST'])
@root_required
def save_software():
    body = request.get_json(silent=True) or {}
    version = body.get('version') or body.get('LATEST_VERSION') or ''
    comment = body.get('comment') if 'comment' in body else body.get('note', '')
    success, msg, data = software_ctrl.save_software_admin(version, comment)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if msg.startswith('请填写') or '不能超过' in msg or '过长' in msg else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@software_bp.route('/api/changelog', methods=['GET'])
@root_required
def software_changelog():
    include_client = request.args.get('client', '1').strip().lower() not in (
        '0', 'false', 'no', 'off',
    )
    include_backend = request.args.get('backend', '0').strip().lower() in (
        '1', 'true', 'yes', 'on',
    )
    try:
        max_count = int(request.args.get('max_count') or DEFAULT_MAX_COUNT)
    except (TypeError, ValueError):
        max_count = DEFAULT_MAX_COUNT
    success, msg, data = software_ctrl.get_software_changelog(
        include_client=include_client,
        include_backend=include_backend,
        max_count=max_count,
    )
    return jsonify({'code': 200 if success else 500, 'msg': msg, 'data': data})
