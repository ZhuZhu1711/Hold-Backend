"""
Hold Record 处置流转 API。

规则见 dispose_api.md / dispose_ctrl。
流转记录查询：任意登录角色可读，不按型号归属过滤。
"""
from flask import Blueprint, request, jsonify, session, render_template

from app.controllers import dispose_ctrl
from app.utils.auth_decorators import (
    login_required,
    role_required,
    ROLE_ROOT,
    ROLE_ENGINEER,
    is_root,
    current_role_name,
)

dispose_bp = Blueprint('dispose', __name__, url_prefix='/admin/hold')


def _actor():
    return session.get('user_id'), session.get('role')


# ==========================================
# 页面
# ==========================================

@dispose_bp.route('/circulations')
@login_required
def circulations_page():
    """Hold Record 流转报表（全角色可读，含他人型号）。"""
    return render_template(
        'hold/circulations.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
    )


@dispose_bp.route('/api/dispose_actions', methods=['GET'])
@role_required(ROLE_ROOT, ROLE_ENGINEER)
def api_dispose_actions():
    """全部可发起的处置行为码说明。"""
    success, msg, data = dispose_ctrl.list_dispose_actions()
    return jsonify({'code': 200, 'msg': msg, 'data': data})


@dispose_bp.route('/api/dispose', methods=['POST'])
@role_required(ROLE_ROOT, ROLE_ENGINEER)
def api_dispose():
    """
    执行一次处置流转。
    Body JSON:
      hold_record_id  (必填)
      dispose         (必填，见 dispose_api.md)
      dispose_detail  (可选，备注，最长 100)
    """
    data = request.get_json(silent=True) or {}
    hold_record_id = data.get('hold_record_id')
    dispose = data.get('dispose')
    dispose_detail = data.get('dispose_detail')

    if hold_record_id is None or dispose is None:
        return jsonify({'code': 400, 'msg': 'hold_record_id 与 dispose 必填', 'data': None}), 400

    actor_user_id, actor_role = _actor()
    success, msg, result = dispose_ctrl.dispose_record(
        hold_record_id=hold_record_id,
        dispose=dispose,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        dispose_detail=dispose_detail,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': result})

    bad_keys = ('不存在', '无效', '必填', '不可', '仅', '已关闭', '最长', '不支持', '无当前')
    status = 400 if any(k in msg for k in bad_keys) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@dispose_bp.route('/api/circulations', methods=['GET'])
@login_required
def api_query_circulations():
    """
    流转记录查询（全角色可读，含他人型号）。
    Query:
      hold_record_id  指定 record
      product_id      型号（模糊）
      wafer_id / lot_id
      dispose         行为码
      keyword         wafer/lot/型号/hold_code/备注
      limit           默认 500，最大 5000
    """
    success, msg, data = dispose_ctrl.query_circulations(
        hold_record_id=request.args.get('hold_record_id'),
        product_id=request.args.get('product_id', '').strip(),
        wafer_id=request.args.get('wafer_id', '').strip(),
        lot_id=request.args.get('lot_id', '').strip(),
        dispose=request.args.get('dispose'),
        keyword=request.args.get('keyword', '').strip(),
        limit=request.args.get('limit', 500),
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data, 'total': len(data)})
    status = 400 if ('无效' in msg) else 500
    return jsonify({'code': status, 'msg': msg, 'data': []}), status


@dispose_bp.route('/api/records/<int:record_id>/circulations', methods=['GET'])
@login_required
def api_circulations(record_id):
    """
    某 hold_record 的流转历史（全角色可读，含他人型号）。
    返回 record 摘要 + circulations 列表。
    """
    success, msg, data = dispose_ctrl.get_circulations(record_id)
    if success:
        total = len(data.get('circulations') or [])
        return jsonify({'code': 200, 'msg': msg, 'data': data, 'total': total})
    status = 404 if '不存在' in msg else (400 if '无效' in msg else 500)
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@dispose_bp.route('/api/pending_records', methods=['GET'])
@role_required(ROLE_ROOT, ROLE_ENGINEER)
def api_pending_records():
    """
    待办列表：最新流转 NEXT_OWNER_ID = 当前用户（root 默认全量，可传 owner_id 过滤）。
    Query: product_id, keyword, limit, owner_id(仅 root)
    """
    product_id = request.args.get('product_id', '').strip()
    keyword = request.args.get('keyword', '').strip()
    limit = request.args.get('limit', 500)

    actor_user_id, _ = _actor()
    if is_root():
        owner_raw = request.args.get('owner_id', None)
        if owner_raw is None or str(owner_raw).strip() == '':
            owner_id = None
        else:
            try:
                owner_id = int(owner_raw)
            except (TypeError, ValueError):
                return jsonify({'code': 400, 'msg': 'owner_id 无效', 'data': []}), 400
    else:
        owner_id = actor_user_id

    success, msg, data = dispose_ctrl.get_pending_records(
        owner_id=owner_id,
        product_id=product_id,
        keyword=keyword,
        limit=limit,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data, 'total': len(data)})
    return jsonify({'code': 500, 'msg': msg, 'data': []}), 500
