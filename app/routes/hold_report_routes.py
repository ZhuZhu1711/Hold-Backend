from datetime import datetime
import io
import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Blueprint, render_template, request, jsonify, session, Response, send_file

from app.controllers import hold_report_ctrl, hold_merge_fail_ctrl, hold_info_export_ctrl, manual_hold_ctrl
from app.controllers.defect_code_ctrl import query_bincode_defect
from app.utils.auth_decorators import (
    root_required,
    login_required,
    role_required,
    current_role_name,
    API_TOKEN_HEADER,
    API_TOKEN_USER_NAME,
    ROLE_ROOT,
    ROLE_ENGINEER,
    ROLE_PRODUCTION,
)
from app.utils.excel_export import stamp_filename, xlsx_or_error

hold_report_bp = Blueprint('hold_report', __name__, url_prefix='/admin/hold')

# 手提 Hold API 调用日志（外部 Token / 页面 Session 共用）
_manual_hold_logger = logging.getLogger('manual_hold_api')
_manual_hold_logger.setLevel(logging.INFO)
_manual_hold_logger.propagate = False
if not _manual_hold_logger.handlers:
    if not os.path.exists('logs'):
        os.makedirs('./logs')
    _mh_handler = RotatingFileHandler(
        'logs/manual_hold.log',
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8',
    )
    _mh_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    _manual_hold_logger.addHandler(_mh_handler)


def _log_manual_hold_api(payload, upload_count, success, msg, data, status):
    """记一条简易调用日志：关键字段 + 结果，不写完整 body。"""
    raw = payload if isinstance(payload, dict) else {}
    forwarded = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    client_ip = forwarded or (request.remote_addr or '')
    via = 'token' if (
        request.headers.get(API_TOKEN_HEADER)
        or session.get('user_name') == API_TOKEN_USER_NAME
    ) else 'session'
    record_id = (data or {}).get('ID') if success else None
    _manual_hold_logger.log(
        logging.INFO if success else logging.WARNING,
        'via=%s status=%s ok=%s id=%s line=%s product=%s lot=%s wafer=%s '
        'hold_code=%s station=%s uploads=%s emp=%s user=%s ip=%s msg=%s',
        via,
        status,
        success,
        record_id,
        raw.get('line') or raw.get('LINE') or '',
        raw.get('product_id') or raw.get('PRODUCT_ID') or '',
        raw.get('lot_id') or raw.get('LOT_ID') or '',
        raw.get('wafer_id') or raw.get('WAFER_ID') or '',
        raw.get('hold_code') or raw.get('HOLD_CODE') or '',
        raw.get('station') or raw.get('STATION') or '',
        upload_count,
        session.get('employee_no') or '',
        session.get('user_name') or '',
        client_ip,
        msg or '',
    )


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
@role_required(ROLE_ROOT, ROLE_ENGINEER)
def hold_info_export_page():
    """FT_HOLD_RECORD 按型号+时间导出 FT ATE Hold Lot（root 全量 / 工程师仅所属型号）"""
    nav_area = 'eng' if session.get('role') == ROLE_ENGINEER else 'admin'
    return render_template(
        'hold/export.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
        nav_area=nav_area,
        products_api=(
            '/eng/api/products' if nav_area == 'eng' else '/admin/hold/api/products'
        ),
    )


@hold_report_bp.route('/manual')
@role_required(ROLE_ROOT, ROLE_ENGINEER, ROLE_PRODUCTION)
def manual_hold_page():
    """手提 Hold 料创建页（root / 工程师 / 生产）。"""
    role = session.get('role')
    if role == ROLE_ENGINEER:
        nav_area = 'eng'
    elif role == ROLE_PRODUCTION:
        nav_area = 'prod'
    else:
        nav_area = 'admin'
    return render_template(
        'hold/manual_hold.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
        nav_area=nav_area,
        **manual_hold_ctrl.manual_hold_page_options(),
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


def _collect_upload_files():
    files = []
    seen = set()
    for key in ('files', 'images', 'annex'):
        for item in request.files.getlist(key):
            if not item or not item.filename:
                continue
            ident = id(item)
            if ident in seen:
                continue
            seen.add(ident)
            files.append((item.filename, item.read()))
    return files


def _manual_hold_payload():
    ctype = (request.content_type or '').lower()
    if 'application/json' in ctype:
        return request.get_json(silent=True) or {}
    data = request.form.to_dict(flat=True)
    paths = request.form.getlist('annex_paths')
    if paths:
        data['annex_paths'] = paths
    nos = request.form.getlist('wafer_nos')
    if nos:
        data['wafer_nos'] = nos
    return data


@hold_report_bp.route('/api/manual_hold', methods=['POST'])
@role_required(ROLE_ROOT, ROLE_ENGINEER, ROLE_PRODUCTION)
def api_manual_hold():
    """
    创建手提 Hold Record（SOURCE=1）。
    JSON 或 multipart：line=FT|WLT，product_id / station / equip_id / lot_id /
    wafer_id / hold_reason；WLT 须 hold_code=004|022，STATION 固定 WLT2。
    附件：annex_ftp_path / annex_paths，或 files/images 上传。
    工程师仅可创建所属型号。
    """
    payload = _manual_hold_payload()
    uploaded = _collect_upload_files()
    success, msg, data = manual_hold_ctrl.create_manual_hold(
        payload,
        uploaded_files=uploaded,
        operator=session.get('user_name') or '',
        actor_role=session.get('role'),
        actor_user_id=session.get('user_id'),
    )
    if success:
        status = 200
    elif '不属于' in msg:
        status = 403
    elif any(k in msg for k in ('须', '缺少', '要求', '不支持', '过大', '为空', '匹配', '相同', '超过')):
        status = 400
    else:
        status = 500
    _log_manual_hold_api(payload, len(uploaded), success, msg, data, status)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/manual_hold/recent', methods=['GET'])
@role_required(ROLE_ROOT, ROLE_ENGINEER, ROLE_PRODUCTION)
def api_manual_hold_recent():
    """最近手提 Hold Record（SOURCE=1）。工程师仅看所属型号。"""
    owner_eng_id = None
    if session.get('role') == ROLE_ENGINEER:
        owner_eng_id = session.get('user_id')
    success, msg, data = manual_hold_ctrl.list_recent_manual_holds(
        request.args.get('limit', 20),
        owner_eng_id=owner_eng_id,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    return jsonify({'code': 500, 'msg': msg, 'data': []}), 500


@hold_report_bp.route('/api/manual_hold/products', methods=['GET'])
@role_required(ROLE_ROOT, ROLE_ENGINEER, ROLE_PRODUCTION)
def api_manual_hold_products():
    """手提型号智能匹配：PRODUCT_INFO，按产线后缀过滤。工程师仅所属型号。"""
    owner_eng_id = None
    if session.get('role') == ROLE_ENGINEER:
        owner_eng_id = session.get('user_id')
    success, msg, data = manual_hold_ctrl.list_manual_hold_products(
        request.args.get('line', '').strip(),
        keyword=request.args.get('keyword', '').strip(),
        owner_eng_id=owner_eng_id,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if '须为' in msg else 500
    return jsonify({'code': status, 'msg': msg, 'data': []}), status


@hold_report_bp.route('/api/annex_image', methods=['GET'])
@login_required
def api_annex_image():
    """
    按 hold_record 下载 ANNEX_FTP_PATH 中第 index 张图（从 0 起）。
    Query: record_id, index
    """
    success, msg, payload = manual_hold_ctrl.get_annex_image(
        request.args.get('record_id'),
        request.args.get('index', 0),
    )
    if success:
        as_attachment = str(request.args.get('download') or '').lower() in ('1', 'true', 'yes')
        return send_file(
            io.BytesIO(payload['bytes']),
            mimetype=payload.get('mimetype') or 'application/octet-stream',
            download_name=payload.get('filename') or 'annex',
            as_attachment=as_attachment,
        )
    if '不存在' in msg or '无附件' in msg or '超出' in msg:
        status = 404
    elif any(k in msg for k in ('无效', '须为')):
        status = 400
    else:
        status = 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/annex_zip', methods=['GET'])
@login_required
def api_annex_zip():
    """打包下载该 hold_record 全部附件。Query: record_id"""
    success, msg, payload = manual_hold_ctrl.get_annex_zip(request.args.get('record_id'))
    if success:
        return send_file(
            io.BytesIO(payload['bytes']),
            mimetype=payload.get('mimetype') or 'application/zip',
            download_name=payload.get('filename') or 'annex.zip',
            as_attachment=True,
        )
    if '不存在' in msg or '无附件' in msg:
        status = 404
    elif any(k in msg for k in ('无效', '须为')):
        status = 400
    else:
        status = 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


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


@hold_report_bp.route('/api/wafer_yield', methods=['GET'])
@login_required
def api_wafer_yield():
    """
    按 product_id + wafer_id 查询 VW_WAFER_YIELD。
    Query: product_id, wafer_id 必填；wafer_id 为 #03 / #01#02 时 lot_id 必填。
    多片按展开顺序返回 items[].yield，不聚合。
    """
    product_id = request.args.get('product_id', '').strip()
    lot_id = request.args.get('lot_id', '').strip()
    wafer_id = request.args.get('wafer_id', '').strip()
    success, msg, data = hold_report_ctrl.get_wafer_yield(
        product_id=product_id,
        lot_id=lot_id or None,
        wafer_id=wafer_id,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if (
        '请指定' in msg or '需同时' in msg or '无效' in msg
    ) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/wafer_yield/batch', methods=['POST'])
@login_required
def api_wafer_yield_batch():
    """
    批量查询 VW_WAFER_YIELD。
    Body: { "items": [ { "key", "product_id", "lot_id", "wafer_id" }, ... ] }
    """
    body = request.get_json(silent=True) or {}
    items = body.get('items') if isinstance(body, dict) else None
    success, msg, data = hold_report_ctrl.get_wafer_yield_batch(items)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 400 if ('请指定' in msg or '上限' in msg) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/hold_count', methods=['GET'])
@login_required
def api_hold_count():
    """
    按 wafer_id 统计 hold_record 次数。
    Query: wafer_id（非梓一时必填）；lot_id 可选（展示串 #05 / #01#02 时建议带上）；
    hold_wafer_attr 可选，梓一合批（bit1=2）时按 LOT_ID 精确匹配且 lot_id 必填。
    """
    wafer_id = request.args.get('wafer_id', '').strip()
    lot_id = request.args.get('lot_id', '').strip()
    hold_wafer_attr = request.args.get('hold_wafer_attr', '').strip()
    success, msg, data = hold_report_ctrl.get_hold_count_by_wafer(
        wafer_id,
        lot_id=lot_id or None,
        hold_wafer_attr=hold_wafer_attr or None,
    )
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
        'route_id': request.args.get('route_id', '').strip(),
        'start_dttm': request.args.get('start_dttm', '').strip(),
        'end_dttm': request.args.get('end_dttm', '').strip(),
        'sub_customer': request.args.get('sub_customer', '').strip(),
        'package_type': request.args.get('package_type', '').strip(),
        'factory': request.args.get('factory', '').strip(),
        'area': request.args.get('area', '').strip(),
    }


def _export_owner_eng_id():
    if session.get('role') == ROLE_ENGINEER:
        return session.get('user_id')
    return None


def _hold_info_export_status(msg):
    if '不属于' in msg:
        return 403
    if any(k in msg for k in ('请指定', '不能早于', '无效')):
        return 400
    return 500


@hold_report_bp.route('/api/hold_info_export', methods=['GET'])
@role_required(ROLE_ROOT, ROLE_ENGINEER)
def api_hold_info_export_preview():
    """预览 FT_HOLD_RECORD 导出结果（最多 100 条）。工程师仅所属型号。"""
    success, msg, data = hold_info_export_ctrl.preview_hold_info_export(
        owner_eng_id=_export_owner_eng_id(),
        **_export_query_args(),
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = _hold_info_export_status(msg)
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@hold_report_bp.route('/api/hold_info_export/xlsx', methods=['GET'])
@role_required(ROLE_ROOT, ROLE_ENGINEER)
def api_hold_info_export_xlsx():
    """导出 FT_HOLD_RECORD 为 FT ATE Hold Lot xlsx（最多 5000 条）。工程师仅所属型号。"""
    args = _export_query_args()
    success, msg, content = hold_info_export_ctrl.export_hold_info_xlsx(
        owner_eng_id=_export_owner_eng_id(),
        **args,
    )
    if not success:
        status = _hold_info_export_status(msg)
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
