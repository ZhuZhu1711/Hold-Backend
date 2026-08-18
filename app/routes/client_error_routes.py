"""客户端崩溃上报接口。不强制登录：崩溃时 session 可能已不可用。"""

from flask import Blueprint, jsonify, request, session

from app.controllers import client_error_ctrl

client_error_bp = Blueprint('client_error', __name__, url_prefix='/api')


@client_error_bp.route('/client_errors', methods=['POST'])
def create_client_error():
    """
    Body JSON:
      report_id (必填 UUID)
      event_type: UNCAUGHT / THREAD_UNCAUGHT / QT_FATAL
      message / stack_trace 至少其一
      occurred_at, exception_type, hostname, os_user,
      employee_no, user_id, app_mode, frozen
    """
    body = request.get_json(silent=True) or {}
    forwarded = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    ok, status, msg, data = client_error_ctrl.save_client_error(
        body,
        session_user_id=session.get('user_id'),
        session_employee_no=session.get('employee_no'),
        client_ip=forwarded or (request.remote_addr or ''),
    )
    http_status = 200 if ok else status
    return jsonify({'code': 200 if ok else status, 'msg': msg, 'data': data}), http_status
