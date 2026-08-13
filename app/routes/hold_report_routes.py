from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, session, Response

from app.controllers import hold_report_ctrl, hold_merge_fail_ctrl, hold_info_export_ctrl
from app.controllers.defect_code_ctrl import query_bincode_defect
from app.utils.auth_decorators import root_required, login_required, current_role_name
from app.utils.excel_export import stamp_filename, xlsx_or_error

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


@hold_report_bp.route('/merge_failed')
@root_required
def merge_failed_page():
    """Merge 失败 hold_info 处理页（root）"""
    return render_template(
        'hold/merge_failed.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
    )


@hold_report_bp.route('/export')
@root_required
def hold_info_export_page():
    """FT_HOLD_RECORD 按型号+时间导出 FT ATE Hold Lot（root）"""
    return render_template(
        'hold/export.html',
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


@hold_report_bp.route('/api/holding_records/export', methods=['GET'])
@root_required
def api_holding_records_export():
    """
    导出在线 Hold Record 为 xlsx（筛选条件与列表一致，最多 5000 行）。
    Query: product_id, station, keyword, record_type(0/1/2)
    """
    success, msg, content = hold_report_ctrl.export_holding_records_xlsx(
        product_id=request.args.get('product_id', '').strip(),
        station=request.args.get('station', '').strip(),
        keyword=request.args.get('keyword', '').strip(),
        record_type=request.args.get('record_type', '').strip() or None,
    )
    return xlsx_or_error(
        success, msg, content, stamp_filename('holding_records'),
        bad_keys=('无效', '须为'),
    )


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
    Hold Record 数据分析（bysite + raw_data + 同 lot 片列表）。
    Query: wafer_id (必填), lot_id（展示串 #03 时必填；同 lot 分支也依赖原始 LOT_ID）,
           record_type, station（必填，仅 WLT2 / FATE-FA / VBOX-FA）
    同 lot：见 hold_report_ctrl.get_hold_analysis / docs/03-数据分析.md
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
    status = 400 if (
        '请指定' in msg
        or '无效' in msg
        or '需同时' in msg
        or 'station 仅支持' in msg
    ) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/bincode_defect', methods=['GET'])
@login_required
def api_bincode_defect():
    """
    按产品型号查询 bincode ↔ defect 映射（DEFECT_CODE）。
    Query: product_id（必填，PRODUCT_INFO.PRODUCT_ID）
    """
    product_id = request.args.get('product_id', '').strip()
    success, msg, data = query_bincode_defect(product_id)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if '请指定' in msg else 500
    return jsonify({'code': status, 'msg': msg, 'data': []}), status


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


@hold_report_bp.route('/api/history/export', methods=['GET'])
@root_required
def api_hold_history_export():
    """
    导出 Hold 历史数量为 xlsx（与柱状图同一筛选）。
    Query: product_id, period_type, year, month, week
    """
    success, msg, content = hold_report_ctrl.export_hold_history_xlsx(
        product_id=request.args.get('product_id', '').strip(),
        period_type=request.args.get('period_type', 'month').strip(),
        year=request.args.get('year'),
        month=request.args.get('month'),
        week=request.args.get('week'),
    )
    return xlsx_or_error(
        success, msg, content, stamp_filename('hold_history'),
        bad_keys=('请指定', '必须', '无效', '须为', '不存在'),
    )


@hold_report_bp.route('/api/products', methods=['GET'])
@root_required
def api_hold_products():
    """历史报表型号下拉选项。"""
    keyword = request.args.get('keyword', '').strip()
    success, msg, data = hold_report_ctrl.get_hold_product_options(keyword)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    return jsonify({'code': 500, 'msg': msg, 'data': []}), 500


def _safe_filename(text):
    return ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in (text or ''))[:40]


def _export_query_args():
    return {
        'product_id': request.args.get('product_id', '').strip(),
        'lot_id': request.args.get('lot_id', '').strip(),
        'start_dttm': request.args.get('start_dttm', '').strip(),
        'end_dttm': request.args.get('end_dttm', '').strip(),
        'sub_customer': request.args.get('sub_customer', '').strip(),
        'package_type': request.args.get('package_type', '').strip(),
        'factory': request.args.get('factory', '').strip(),
        'area': request.args.get('area', '').strip(),
    }


@hold_report_bp.route('/api/hold_info_export', methods=['GET'])
@root_required
def api_hold_info_export_preview():
    """预览 FT_HOLD_RECORD 导出结果（最多 100 条）。"""
    success, msg, data = hold_info_export_ctrl.preview_hold_info_export(**_export_query_args())
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if any(k in msg for k in ('请指定', '不能早于', '无效')) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/hold_info_export/xlsx', methods=['GET'])
@root_required
def api_hold_info_export_xlsx():
    """导出 FT_HOLD_RECORD 为 FT ATE Hold Lot xlsx（最多 5000 条）。"""
    args = _export_query_args()
    success, msg, content = hold_info_export_ctrl.export_hold_info_xlsx(**args)
    if not success:
        status = 400 if any(k in msg for k in ('请指定', '不能早于', '无效')) else 500
        return jsonify({'code': status, 'msg': msg, 'data': None}), status

    product = _safe_filename(args.get('product_id') or 'hold')
    lot = _safe_filename(args.get('lot_id') or '')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'hold_info_{product}_{lot}_{stamp}.xlsx' if lot else f'hold_info_{product}_{stamp}.xlsx'
    return Response(
        content,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
        },
    )


def _merge_fail_operator():
    if session.get('user_id') is not None:
        return f"{session.get('user_id')}:{session.get('user_name')}"
    return session.get('user_name') or ''


@hold_report_bp.route('/api/merge_failed', methods=['GET'])
@root_required
def api_merge_failed_list():
    """
    HOLD_RECORD_ID=-1 的 hold_info 列表。
    Query: product_id, lot_id, wafer_id, station, hold_code, keyword, page, page_size
    """
    success, msg, data = hold_merge_fail_ctrl.list_dirty_hold_infos(
        product_id=request.args.get('product_id', '').strip(),
        lot_id=request.args.get('lot_id', '').strip(),
        wafer_id=request.args.get('wafer_id', '').strip(),
        station=request.args.get('station', '').strip(),
        hold_code=request.args.get('hold_code', '').strip(),
        keyword=request.args.get('keyword', '').strip(),
        page=request.args.get('page', 1),
        page_size=request.args.get('page_size', 20),
    )
    if success:
        return jsonify({
            'code': 200,
            'msg': msg,
            'data': data.get('items') or [],
            'total': data.get('total', 0),
            'page': data.get('page', 1),
            'page_size': data.get('page_size', 20),
            'pages': data.get('pages', 1),
        })
    return jsonify({'code': 500, 'msg': msg, 'data': [], 'total': 0}), 500


@hold_report_bp.route('/api/merge_failed/reset', methods=['POST'])
@root_required
def api_merge_failed_reset():
    """将选中脏 hold_info 重置为 HOLD_RECORD_ID=0，等待下次 merge。"""
    body = request.get_json(silent=True) or {}
    success, msg, data = hold_merge_fail_ctrl.reset_dirty_infos(
        body.get('ids'),
        operator=_merge_fail_operator(),
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if data is not None or '请选择' in msg or '未更新' in msg else 500
    return jsonify({'code': status, 'msg': msg, 'data': data}), status


@hold_report_bp.route('/api/merge_failed/draft', methods=['POST'])
@root_required
def api_merge_failed_draft():
    """按选中脏 hold_info 生成手动提 record 草稿。"""
    body = request.get_json(silent=True) or {}
    success, msg, data = hold_merge_fail_ctrl.build_manual_draft(body.get('ids'))
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if any(k in msg for k in ('请选择', '非脏', '草稿')) else 500
    return jsonify({'code': status, 'msg': msg, 'data': data}), status


@hold_report_bp.route('/api/merge_failed/create', methods=['POST'])
@root_required
def api_merge_failed_create():
    """Root 确认草稿字段后，从脏 hold_info 手动创建 hold_record。"""
    body = request.get_json(silent=True) or {}
    success, msg, data = hold_merge_fail_ctrl.create_record_from_dirty(
        body.get('ids'),
        body.get('record'),
        operator=_merge_fail_operator(),
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if any(
        k in msg for k in ('请选择', '缺少', '须为', '非脏', '失败')
    ) else 500
    return jsonify({'code': status, 'msg': msg, 'data': data}), status
