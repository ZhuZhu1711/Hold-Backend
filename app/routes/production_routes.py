"""
生产工作台：生产节点 Hold 查看/处置、导出 xlsx、Hold 流转查询。
"""
from flask import Blueprint, render_template, request, jsonify, session

from app.controllers import production_ctrl, dispose_ctrl, manual_hold_ctrl
from app.utils.auth_decorators import production_required, current_role_name
from app.utils.excel_export import stamp_filename, xlsx_or_error

production_bp = Blueprint('production', __name__, url_prefix='/prod')


def _page_ctx(**extra):
    ctx = {
        'user_name': session.get('user_name'),
        'role_name': current_role_name(),
    }
    ctx.update(extra)
    return ctx


def _actor():
    return session.get('user_id'), session.get('role')


# ==========================================
# 页面
# ==========================================

@production_bp.route('/')
@production_bp.route('/dashboard')
@production_required
def dashboard():
    return render_template('prod/dashboard.html', **_page_ctx())


@production_bp.route('/holds')
@production_required
def holds_page():
    return render_template('prod/holds.html', **_page_ctx())


@production_bp.route('/dispose/<int:record_id>')
@production_required
def dispose_page(record_id):
    """生产处置页：留样完成 / 回退 / 关闭。"""
    return render_template('prod/dispose.html', **_page_ctx(record_id=record_id))


@production_bp.route('/circulations')
@production_required
def circulations_page():
    """Hold 流转查询：与工程师同源 API，全量可读。"""
    return render_template('prod/circulations.html', **_page_ctx())


@production_bp.route('/manual')
@production_required
def manual_hold_page():
    """手提 Hold 料创建。已下架。"""
    return manual_hold_ctrl.gone_response()


# ==========================================
# API
# ==========================================

@production_bp.route('/api/holding_records', methods=['GET'])
@production_required
def api_holding_records():
    """
    当前节点在生产，或待留样的在线 Hold 列表。
    Query: record_type(0/1/2), product_id, station, keyword, page, page_size
    """
    success, msg, payload = production_ctrl.get_production_holding_records(
        product_id=request.args.get('product_id', '').strip(),
        station=request.args.get('station', '').strip(),
        keyword=request.args.get('keyword', '').strip(),
        record_type=request.args.get('record_type'),
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
    status = 400 if ('无效' in msg or '须为' in msg) else 500
    return jsonify({'code': status, 'msg': msg, 'data': [], 'total': 0}), status


@production_bp.route('/api/holding_records/export', methods=['GET'])
@production_required
def api_holding_records_export():
    """
    导出生产节点 Hold 列表为 xlsx（筛选条件与列表一致，最多 5000 行）。
    Query: record_type, product_id, station, keyword
    """
    success, msg, content = production_ctrl.export_production_holding_records_xlsx(
        product_id=request.args.get('product_id', '').strip(),
        station=request.args.get('station', '').strip(),
        keyword=request.args.get('keyword', '').strip(),
        record_type=request.args.get('record_type'),
    )
    return xlsx_or_error(
        success, msg, content, stamp_filename('production_holds'),
        bad_keys=('无效', '须为'),
    )


@production_bp.route('/api/records/<int:record_id>', methods=['GET'])
@production_required
def api_dispose_record(record_id):
    """生产处置页：加载 record 摘要（生产节点或待留样）。"""
    success, msg, data = production_ctrl.get_production_dispose_record(record_id)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 404 if '不存在' in msg else (400 if ('无效' in msg or '不在生产' in msg or '无需留样' in msg) else 500)
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@production_bp.route('/api/dispose_actions', methods=['GET'])
@production_required
def api_dispose_actions():
    """生产可发起的处置行为（dispose_api.md「生产处置」）。"""
    success, msg, data = dispose_ctrl.list_dispose_actions(group='production')
    # UI 主推 65/8/99
    return jsonify({'code': 200, 'msg': msg, 'data': data})


@production_bp.route('/api/dispose', methods=['POST'])
@production_required
def api_dispose():
    """
    生产处置（dispose_api.md「生产处置」）。
    Body JSON:
      hold_record_id  (必填)
      dispose         (必填：65 留样完成 / 8 回退 / 99 关闭)
      dispose_detail  (可选，兼容旧字段)
      dispose_manual_note (可选，手输备注)
    """
    data = request.get_json(silent=True) or {}
    hold_record_id = data.get('hold_record_id')
    dispose = data.get('dispose')
    dispose_detail = data.get('dispose_detail')
    dispose_manual_note = data.get('dispose_manual_note')

    if hold_record_id is None or dispose is None:
        return jsonify({'code': 400, 'msg': 'hold_record_id 与 dispose 必填', 'data': None}), 400

    actor_user_id, actor_role = _actor()
    success, msg, result = dispose_ctrl.dispose_production_record(
        hold_record_id=hold_record_id,
        dispose=dispose,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        dispose_detail=dispose_detail,
        dispose_manual_note=dispose_manual_note,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': result})

    bad_keys = (
        '不存在', '无效', '必填', '不可', '仅', '已关闭', '最长', '不支持',
        '无当前', '非生产', '关闭', '分析', '留样', '无需',
    )
    status = 400 if any(k in msg for k in bad_keys) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status
