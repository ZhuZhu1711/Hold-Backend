from flask import Blueprint, render_template, request, jsonify, session

from app.controllers import hold_report_ctrl
from app.utils.auth_decorators import root_required, login_required, current_role_name

hold_report_bp = Blueprint('hold_report', __name__, url_prefix='/admin/hold')


# ==========================================
# 页面
# ==========================================

@hold_report_bp.route('/holding')
@root_required
def holding_record_page():
    """当前在线 Hold Record 报表（root）"""
    return render_template(
        'hold/holding_records.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
    )


@hold_report_bp.route('/history')
@root_required
def hold_history_page():
    """Hold 历史数量柱状图（root）"""
    return render_template(
        'hold/history.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
    )


# ==========================================
# API
# ==========================================

@hold_report_bp.route('/api/holding_records', methods=['GET'])
@root_required
def api_holding_records():
    """
    当前仍在 hold 的 record 列表（分页）。
    Query: product_id, station, keyword, record_type(0/1/2), page, page_size
    record_type 对应处置单大类：0=FT异常反馈单 1=FVI异常反馈单 2=WLT异常反馈单
    """
    product_id = request.args.get('product_id', '').strip()
    station = request.args.get('station', '').strip()
    keyword = request.args.get('keyword', '').strip()
    record_type = request.args.get('record_type', '').strip()
    page = request.args.get('page', 1)
    page_size = request.args.get('page_size', 20)

    success, msg, payload = hold_report_ctrl.get_holding_records(
        product_id=product_id,
        station=station,
        keyword=keyword,
        record_type=record_type if record_type != '' else None,
        page=page,
        page_size=page_size,
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


@hold_report_bp.route('/api/fvi_defect_details', methods=['GET'])
@root_required
def api_fvi_defect_details():
    """
    FVI 异常反馈单缺陷明细。
    Query: lot_id (必填), line_type (默认 FT)
    DEFECT_CODE 返回已截取最后一个 '-' 后的短码；另附 summary 组合文案。
    """
    lot_id = request.args.get('lot_id', '').strip()
    line_type = request.args.get('line_type', 'FT').strip() or 'FT'
    success, msg, data = hold_report_ctrl.get_fvi_defect_details(
        lot_id=lot_id,
        line_type=line_type,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if '请指定' in msg else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/hold_count', methods=['GET'])
@login_required
def api_hold_count():
    """
    按 wafer_id 统计 hold_record 次数。
    Query: wafer_id (必填)
    """
    wafer_id = request.args.get('wafer_id', '').strip()
    success, msg, data = hold_report_ctrl.get_hold_count_by_wafer(wafer_id)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if ('请指定' in msg or '无效' in msg) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/split_merge_history', methods=['GET'])
@login_required
def api_split_merge_history():
    """
    查询 wafer 合批记录（MES SPLIT_MERGE_HISTORY）。
    Query: wafer_id (必填；合批目标 id，通常含 '-' 且后缀数字 > 2 位)
    """
    wafer_id = request.args.get('wafer_id', '').strip()
    success, msg, data = hold_report_ctrl.get_split_merge_history(wafer_id)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if ('请指定' in msg or '无效' in msg) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/analysis', methods=['GET'])
@login_required
def api_hold_analysis():
    """
    Hold Record 数据分析（bysite + raw_data；合批附源 wafer raw_data）。
    Query: wafer_id (必填), lot_id（展示串 #03 时必填）, record_type, station
    """
    wafer_id = request.args.get('wafer_id', '').strip()
    lot_id = request.args.get('lot_id', '').strip()
    record_type = request.args.get('record_type', '').strip()
    station = request.args.get('station', '').strip()
    success, msg, data = hold_report_ctrl.get_hold_analysis(
        wafer_id=wafer_id,
        record_type=record_type if record_type != '' else None,
        station=station or None,
        lot_id=lot_id or None,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if ('请指定' in msg or '无效' in msg or '需同时' in msg) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/history', methods=['GET'])
@root_required
def api_hold_history():
    """
    Hold 历史簇状柱状图数据（按处置单 RECORD_TYPE 拆分）。
    Query:
      product_id  (必填)
      period_type month | week
      year
      month       (period_type=month 时必填, 1-12)
      week        (period_type=week 时必填, ISO 周 1-53)
    返回 series: [{record_type, name, values, total}, ...]
      0=FT异常反馈单 1=FVI异常反馈单 2=WLT异常反馈单
    """
    product_id = request.args.get('product_id', '').strip()
    period_type = request.args.get('period_type', 'month').strip()
    year = request.args.get('year')
    month = request.args.get('month')
    week = request.args.get('week')

    success, msg, data = hold_report_ctrl.get_hold_history(
        product_id=product_id,
        period_type=period_type,
        year=year,
        month=month,
        week=week,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})

    bad_request_keys = ('请指定', '必须', '无效', '须为', '不存在')
    status = 400 if any(k in msg for k in bad_request_keys) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/products', methods=['GET'])
@root_required
def api_hold_products():
    """历史报表型号下拉选项。"""
    keyword = request.args.get('keyword', '').strip()
    success, msg, data = hold_report_ctrl.get_hold_product_options(keyword)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    return jsonify({'code': 500, 'msg': msg, 'data': []}), 500
