"""向上游提交异常反馈。调用方需登录。"""
from flask import Blueprint, jsonify, request

from app.controllers.abnormal_feedback_ctrl import submit_abnormal_feedback
from app.utils.auth_decorators import login_required

abnormal_feedback_bp = Blueprint('abnormal_feedback', __name__, url_prefix='/api')


@abnormal_feedback_bp.route('/abnormal_feedback', methods=['POST'])
@login_required
def create_abnormal_feedback():
    source = request.get_json(silent=True)
    ok, status, msg, data = submit_abnormal_feedback(source)
    http_status = 200 if ok else status
    code = 200 if ok else status
    return jsonify({'code': code, 'msg': msg, 'data': data}), http_status
