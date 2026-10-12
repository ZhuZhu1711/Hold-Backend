from flask import Blueprint, jsonify, render_template, request, session

from app.controllers.defect_code_sync_ctrl import query_sync_logs
from app.utils.auth_decorators import current_role_name, root_required

defect_sync_bp = Blueprint('defect_sync', __name__, url_prefix='/admin/defect-sync')


@defect_sync_bp.route('')
@root_required
def defect_sync_page():
    return render_template(
        'defect/sync_log.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
    )


@defect_sync_bp.route('/api', methods=['GET'])
@root_required
def defect_sync_api():
    success, msg, data = query_sync_logs(
        product_code=request.args.get('product_code', ''),
        prod_line=request.args.get('prod_line', ''),
        status=request.args.get('status', ''),
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if msg in ('产线只能是 FT 或 WLT', '状态无效') else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status
