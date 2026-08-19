"""
产品工程师页面与 API。

范围：仅 PRODUCT_INFO.PRO_ENG_ID = 当前用户 的型号。
功能：
  1. 维护所属型号缺陷 code / BSL
  2. 查看所属型号 Hold Record
  3. 手提 Hold 料（所属型号）
"""
from flask import Blueprint, render_template, request, jsonify, session

from app.controllers import engineer_ctrl, dispose_ctrl
from app.utils.auth_decorators import engineer_required, current_role_name
from app.utils.excel_export import stamp_filename, xlsx_or_error

engineer_bp = Blueprint('engineer', __name__, url_prefix='/eng')


def _eng_id():
    return session.get('user_id')


def _page_ctx(**extra):
    ctx = {
        'user_name': session.get('user_name'),
        'role_name': current_role_name(),
    }
    ctx.update(extra)
    return ctx


# ==========================================
# 页面
# ==========================================

@engineer_bp.route('/')
@engineer_bp.route('/dashboard')
@engineer_required
def dashboard():
    return render_template('eng/dashboard.html', **_page_ctx())


@engineer_bp.route('/defects')
@engineer_required
def defects_page():
    return render_template(
        'eng/defects.html',
        **_page_ctx(product_id=request.args.get('product_id', '')),
    )


@engineer_bp.route('/holds')
@engineer_required
def holds_page():
    return render_template('eng/holds.html', **_page_ctx())


@engineer_bp.route('/dispose/<int:record_id>')
@engineer_required
def dispose_page(record_id):
    """工程师处置页：放行/降级/重测/可靠性分析。"""
    return render_template('eng/dispose.html', **_page_ctx(record_id=record_id))


@engineer_bp.route('/circulations')
@engineer_required
def circulations_page():
    """Hold 流转查询：全量可读，不按所属型号过滤（与 root 报表同源）。"""
    return render_template('eng/circulations.html', **_page_ctx())


@engineer_bp.route('/notes')
@engineer_required
def notes_page():
    return render_template(
        'eng/notes.html',
        **_page_ctx(product_id=request.args.get('product_id', '')),
    )


@engineer_bp.route('/manual')
@engineer_required
def manual_hold_page():
    """手提 Hold 料：仅所属型号。"""
    return render_template('hold/manual_hold.html', **_page_ctx(nav_area='eng'))


# ==========================================
# API：所属型号
# ==========================================

@engineer_bp.route('/api/products', methods=['GET'])
@engineer_required
def api_owned_products():
    search = request.args.get('search', '').strip()
    success, msg, data = engineer_ctrl.get_owned_products(_eng_id(), search=search)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    return jsonify({'code': 500, 'msg': msg, 'data': []}), 500


# ==========================================
# API：缺陷 code / BSL
# ==========================================

@engineer_bp.route('/api/defects', methods=['GET'])
@engineer_required
def api_list_defects():
    product_code = request.args.get('product_id', '').strip()
    if not product_code:
        return jsonify({'code': 400, 'msg': '缺少参数 product_id', 'data': []}), 400
    success, msg, data = engineer_ctrl.get_owned_defects(_eng_id(), product_code)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data, 'total': len(data)})
    status = 403 if '不属于' in msg else (404 if '不存在' in msg else 500)
    return jsonify({'code': status, 'msg': msg, 'data': []}), status


@engineer_bp.route('/api/defects', methods=['POST'])
@engineer_required
def api_create_defect():
    data = request.get_json(silent=True) or {}
    success, msg, item = engineer_ctrl.create_owned_defect(_eng_id(), data)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': item})
    status = 403 if '不属于' in msg else 400
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@engineer_bp.route('/api/defects/<int:defect_id>', methods=['PUT'])
@engineer_required
def api_update_defect(defect_id):
    data = request.get_json(silent=True) or {}
    success, msg, item = engineer_ctrl.update_owned_defect(_eng_id(), defect_id, data)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': item})
    status = 403 if '不属于' in msg else (404 if '不存在' in msg else 400)
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@engineer_bp.route('/api/defects/<int:defect_id>', methods=['DELETE'])
@engineer_required
def api_delete_defect(defect_id):
    success, msg = engineer_ctrl.delete_owned_defect(_eng_id(), defect_id)
    if success:
        return jsonify({'code': 200, 'msg': msg})
    status = 403 if '不属于' in msg else (404 if '不存在' in msg else 400)
    return jsonify({'code': status, 'msg': msg}), status


# ==========================================
# API：Hold Record（只读，处置预留）
# ==========================================

@engineer_bp.route('/api/holding_records', methods=['GET'])
@engineer_required
def api_holding_records():
    product_id = request.args.get('product_id', '').strip()
    station = request.args.get('station', '').strip()
    keyword = request.args.get('keyword', '').strip()
    record_type = request.args.get('record_type', '').strip()
    page = request.args.get('page', 1)
    page_size = request.args.get('page_size', 20)
    pending_only = request.args.get('pending_only', '').strip().lower() in (
        '1', 'true', 'yes', 'y',
    )

    success, msg, payload = engineer_ctrl.get_owned_holding_records(
        eng_user_id=_eng_id(),
        product_id=product_id,
        station=station,
        keyword=keyword,
        record_type=record_type if record_type != '' else None,
        page=page,
        page_size=page_size,
        pending_only=pending_only,
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


@engineer_bp.route('/api/holding_records/export', methods=['GET'])
@engineer_required
def api_holding_records_export():
    """
    导出所属型号在线 Hold 为 xlsx（筛选条件与列表一致，最多 5000 行）。
    Query: product_id, station, keyword, record_type, pending_only
    """
    pending_only = request.args.get('pending_only', '').strip().lower() in (
        '1', 'true', 'yes', 'y',
    )
    success, msg, content = engineer_ctrl.export_owned_holding_records_xlsx(
        eng_user_id=_eng_id(),
        product_id=request.args.get('product_id', '').strip(),
        station=request.args.get('station', '').strip(),
        keyword=request.args.get('keyword', '').strip(),
        record_type=request.args.get('record_type', '').strip() or None,
        pending_only=pending_only,
    )
    return xlsx_or_error(
        success, msg, content, stamp_filename('engineer_holds'),
        bad_keys=('无效', '须为'),
    )


@engineer_bp.route('/api/dispose_actions', methods=['GET'])
@engineer_required
def api_dispose_actions():
    """工程师可发起的处置行为：放行/降级/重测/可靠性分析（转交暂屏蔽）。"""
    success, msg, data = dispose_ctrl.list_dispose_actions(group='engineer')
    return jsonify({'code': 200, 'msg': msg, 'data': data})


@engineer_bp.route('/api/records/<int:record_id>', methods=['GET'])
@engineer_required
def api_dispose_record(record_id):
    """处置页加载单条 hold_record（须为所属型号）。"""
    success, msg, data = engineer_ctrl.get_owned_dispose_record(_eng_id(), record_id)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 403 if '不属于' in msg else (404 if '不存在' in msg else 400)
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@engineer_bp.route('/api/dispose', methods=['POST'])
@engineer_required
def api_dispose():
    """
    工程师处置（dispose_api.md「工程师处置」）。
    Body JSON:
      hold_record_id  (必填)
      dispose         (非 WLT 必填：1放行 2降级 3重测 5可靠性分析；转交7暂屏蔽)
                      WLT 可省略，由 wafer_actions 汇总；若传入须与汇总一致
      dispose_detail  (可选，规则化详情；一般由服务端生成；WLT 为直白中文按片描述)
      dispose_note    (可选，工程备注文本，最长 1024)
      dispose_manual_note (可选，手输备注，最长 1024；可靠性分析之后的放行/降级必填)
      confirm_interval (可选，距可靠性分析不足30分钟时须为 true)
      downgrades      (非 WLT 降级：[{from, to}, ...]，服务端生成 DISPOSE_DETAIL)
      retest_grades   (非 WLT 重测等级列表)
      retest_code     (旧版 WLT 单 code，已由 wafer_actions 取代)
      wafer_actions   (WLT 必填：按片处置列表，须覆盖全部 wafer)
    须为当前负责人。工程师处置不改写 GRADE_NUM。
    """
    data = request.get_json(silent=True) or {}
    hold_record_id = data.get('hold_record_id')
    dispose = data.get('dispose')
    dispose_detail = data.get('dispose_detail')
    dispose_note = data.get('dispose_note')
    dispose_manual_note = data.get('dispose_manual_note')
    downgrades = data.get('downgrades')
    retest_grades = data.get('retest_grades')
    retest_code = data.get('retest_code')
    wafer_actions = data.get('wafer_actions')
    confirm_interval = data.get('confirm_interval')

    if hold_record_id is None:
        return jsonify({'code': 400, 'msg': 'hold_record_id 必填', 'data': None}), 400
    if dispose is None and wafer_actions is None:
        return jsonify({
            'code': 400,
            'msg': 'dispose 与 wafer_actions 至少提供其一',
            'data': None,
        }), 400

    success, msg, result = dispose_ctrl.dispose_engineer_record(
        hold_record_id=hold_record_id,
        dispose=dispose,
        actor_user_id=_eng_id(),
        actor_role=session.get('role'),
        dispose_detail=dispose_detail,
        dispose_note=dispose_note,
        dispose_manual_note=dispose_manual_note,
        downgrades=downgrades,
        retest_grades=retest_grades,
        retest_code=retest_code,
        wafer_actions=wafer_actions,
        confirm_interval=confirm_interval,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': result})
    if dispose_ctrl.is_interval_confirm_result(success, result):
        return jsonify({'code': 409, 'msg': msg, 'data': result}), 409

    bad_keys = (
        '不存在', '无效', '必填', '不可', '仅', '已关闭', '最长', '不支持',
        '无当前', '非工程师', '降级', '重测', '互斥', '至少', '不能', '须',
        '缺少', '未知', '重复', 'wafer', '不一致', '提供', '可靠性',
    )
    status = 400 if any(k in msg for k in bad_keys) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@engineer_bp.route('/api/fvi_defect_details', methods=['GET'])
@engineer_required
def api_fvi_defect_details():
    """
    所属型号 FVI 缺陷明细。
    Query: lot_id (必填), line_type (默认 FT)
    """
    lot_id = request.args.get('lot_id', '').strip()
    line_type = request.args.get('line_type', 'FT').strip() or 'FT'
    success, msg, data = engineer_ctrl.get_owned_fvi_defect_details(
        eng_user_id=_eng_id(),
        lot_id=lot_id,
        line_type=line_type,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 403 if '所属' in msg else (400 if '请指定' in msg or '无效' in msg else 500)
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


# ==========================================
# API：工程备注
# ==========================================

@engineer_bp.route('/api/notes', methods=['GET'])
@engineer_required
def api_list_notes():
    product_code = request.args.get('product_id', '').strip()
    if not product_code:
        return jsonify({'code': 400, 'msg': '缺少参数 product_id', 'data': []}), 400
    success, msg, data = engineer_ctrl.get_owned_eng_notes(_eng_id(), product_code)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data, 'total': len(data)})
    status = 403 if '不属于' in msg else (404 if '不存在' in msg else 500)
    return jsonify({'code': status, 'msg': msg, 'data': []}), status


@engineer_bp.route('/api/notes', methods=['POST'])
@engineer_required
def api_create_note():
    data = request.get_json(silent=True) or {}
    success, msg, item = engineer_ctrl.create_owned_eng_note(_eng_id(), data)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': item})
    status = 403 if '不属于' in msg else 400
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@engineer_bp.route('/api/notes/sync', methods=['POST'])
@engineer_required
def api_sync_notes():
    data = request.get_json(silent=True) or {}
    product_code = (
        (data.get('product_id') or data.get('product_code') or '')
        or request.args.get('product_id', '')
    )
    product_code = str(product_code).strip()
    if not product_code:
        return jsonify({'code': 400, 'msg': '缺少参数 product_id', 'data': None}), 400

    success, msg, summary = engineer_ctrl.sync_owned_eng_notes(_eng_id(), product_code)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': summary})
    status = 403 if '不属于' in msg else (
        404 if '不存在' in msg else (502 if 'MES' in msg else 500)
    )
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@engineer_bp.route('/api/notes/<int:note_id>', methods=['PUT'])
@engineer_required
def api_update_note(note_id):
    data = request.get_json(silent=True) or {}
    success, msg, item = engineer_ctrl.update_owned_eng_note(_eng_id(), note_id, data)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': item})
    status = 403 if ('不属于' in msg or '仅可修改' in msg) else (
        404 if '不存在' in msg else 400
    )
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@engineer_bp.route('/api/notes/<int:note_id>', methods=['DELETE'])
@engineer_required
def api_delete_note(note_id):
    success, msg = engineer_ctrl.delete_owned_eng_note(_eng_id(), note_id)
    if success:
        return jsonify({'code': 200, 'msg': msg})
    status = 403 if '不属于' in msg else (404 if '不存在' in msg else 400)
    return jsonify({'code': status, 'msg': msg}), status
