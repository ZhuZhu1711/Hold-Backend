"""CP 客户端接口。当前只有未关闭 Hold 的只读列表。"""
from flask import Blueprint, jsonify, request

from app.controllers import cp_hold_ctrl
from app.utils.auth_decorators import cp_production_required

cp_bp = Blueprint('cp', __name__, url_prefix='/cp')


@cp_bp.route('/api/holding_records', methods=['GET'])
@cp_production_required
def api_holding_records():
    """
    未关闭的 CP Hold。
    Query: product_id, keyword, page, page_size
    """
    success, msg, payload = cp_hold_ctrl.get_cp_holding_records(
        product_id=request.args.get('product_id', '').strip(),
        keyword=request.args.get('keyword', '').strip(),
        page=request.args.get('page', 1),
        page_size=request.args.get('page_size', 20),
    )
    if success:
        return jsonify({
            'code': 200,
            'msg': msg,
            'data': payload.get('items') or [],
            'total': payload.get('total', 0),
            'page': payload.get('page', 1),
            'page_size': payload.get('page_size', 20),
            'pages': payload.get('pages', 1),
        })
    status = 400 if '非法' in msg else 500
    return jsonify({
        'code': status,
        'msg': msg,
        'data': [],
        'total': 0,
    }), status
