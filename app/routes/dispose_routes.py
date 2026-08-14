"""
Hold Record 处置流转 API。

规则见 dispose_api.md / dispose_ctrl。
流转记录查询：任意登录角色可读，不按型号归属过滤。
"""
from flask import Blueprint, request, jsonify, session, render_template

from app.controllers import dispose_ctrl
from app.utils.auth_decorators import (
    role_required,
    ROLE_ROOT,
    ROLE_ENGINEER,
    ROLE_PRODUCTION,
    is_root,
    is_production,
    current_role_name,
)
from app.utils.excel_export import stamp_filename, xlsx_or_error

_CIRCULATION_ROLES = (ROLE_ROOT, ROLE_ENGINEER, ROLE_PRODUCTION)

dispose_bp = Blueprint('dispose', __name__, url_prefix='/admin/hold')


def _actor():
    return session.get('user_id'), session.get('role')


# ==========================================
# 页面
# ==========================================

@dispose_bp.route('/circulations')
@role_required(*_CIRCULATION_ROLES)
def circulations_page():
    """Hold Record 流转报表（root / 工程师 / 生产可读，含他人型号）。"""
    return render_template(
        'hold/circulations.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
    )


@dispose_bp.route('/api/dispose_actions', methods=['GET'])
@role_required(ROLE_ROOT, ROLE_ENGINEER)
def api_dispose_actions():
    """
    可发起的处置行为码说明。
    Query: group=engineer|production|system（可选）
    """
    group = request.args.get('group', '').strip() or None
    success, msg, data = dispose_ctrl.list_dispose_actions(group=group)
    return jsonify({'code': 200, 'msg': msg, 'data': data})


@dispose_bp.route('/api/dispose', methods=['POST'])
@role_required(ROLE_ROOT, ROLE_ENGINEER)
def api_dispose():
    """
    执行一次处置流转（工程师 / root）。
    Body JSON:
      hold_record_id  (必填)
      dispose         (非 WLT 必填；WLT 可由 wafer_actions 汇总)
      dispose_detail  (可选，规则化详情；一般由服务端生成)
      dispose_note    (可选，工程备注，最长 1024)
      dispose_manual_note (可选，手输备注，最长 1024)
      downgrades / retest_grades / retest_code / wafer_actions 同工程师处置 API
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

    actor_user_id, actor_role = _actor()
    # 非 root 走工程师处置约束；root 可代操作全部 USER_DISPOSES
    if is_root():
        success, msg, result = dispose_ctrl.dispose_record(
            hold_record_id=hold_record_id,
            dispose=dispose,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            dispose_detail=dispose_detail,
            dispose_note=dispose_note,
            dispose_manual_note=dispose_manual_note,
            downgrades=downgrades,
            retest_grades=retest_grades,
            retest_code=retest_code,
            wafer_actions=wafer_actions,
            confirm_interval=confirm_interval,
        )
    else:
        success, msg, result = dispose_ctrl.dispose_engineer_record(
            hold_record_id=hold_record_id,
            dispose=dispose,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
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


@dispose_bp.route('/api/production/dispose', methods=['POST'])
@role_required(ROLE_ROOT, ROLE_PRODUCTION)
def api_production_dispose():
    """
    生产处置接口（供生产工作台与外部生产系统联动调用）。
    需先登录；生产角色或生产 OP（USERS.ID=PRODUCTION_OP_ID）可操作，root 可代操作。

    Body JSON:
      hold_record_id  (必填)
      dispose         (必填：65 留样完成 / 8 回退 / 99 关闭)
      dispose_detail  (可选，兼容旧字段；自由备注请用 dispose_manual_note)
      dispose_manual_note (可选，手输备注，最长 1024)

    规则见 dispose_api.md「生产处置」。
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


@dispose_bp.route('/api/circulations', methods=['GET'])
@role_required(*_CIRCULATION_ROLES)
def api_query_circulations():
    """
    流转记录查询（root / 工程师 / 生产可读，含他人型号，分页）。
    Query:
      hold_record_id  指定 record
      product_id      型号（模糊）
      wafer_id / lot_id
      dispose         行为码
      keyword         wafer/lot/型号/hold_code/备注
      page / page_size  默认 1 / 20
    """
    success, msg, payload = dispose_ctrl.query_circulations(
        hold_record_id=request.args.get('hold_record_id'),
        product_id=request.args.get('product_id', '').strip(),
        wafer_id=request.args.get('wafer_id', '').strip(),
        lot_id=request.args.get('lot_id', '').strip(),
        dispose=request.args.get('dispose'),
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
    status = 400 if ('无效' in msg) else 500
    return jsonify({'code': status, 'msg': msg, 'data': [], 'total': 0}), status


@dispose_bp.route('/api/circulations/export', methods=['GET'])
@role_required(*_CIRCULATION_ROLES)
def api_query_circulations_export():
    """
    导出流转记录为 xlsx（筛选条件与列表一致，最多 5000 行）。
    Query: hold_record_id, product_id, wafer_id, lot_id, dispose, keyword
    """
    success, msg, content = dispose_ctrl.export_circulations_xlsx(
        hold_record_id=request.args.get('hold_record_id'),
        product_id=request.args.get('product_id', '').strip(),
        wafer_id=request.args.get('wafer_id', '').strip(),
        lot_id=request.args.get('lot_id', '').strip(),
        dispose=request.args.get('dispose'),
        keyword=request.args.get('keyword', '').strip(),
    )
    return xlsx_or_error(
        success, msg, content, stamp_filename('hold_circulations'),
        bad_keys=('无效',),
    )


@dispose_bp.route('/api/records/<int:record_id>/circulations', methods=['GET'])
@role_required(*_CIRCULATION_ROLES)
def api_circulations(record_id):
    """
    某 hold_record 的流转历史（root / 工程师 / 生产可读，含他人型号）。
    返回 record 摘要 + circulations 列表。
    """
    success, msg, data = dispose_ctrl.get_circulations(record_id)
    if success:
        total = len(data.get('circulations') or [])
        return jsonify({'code': 200, 'msg': msg, 'data': data, 'total': total})
    status = 404 if '不存在' in msg else (400 if '无效' in msg else 500)
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@dispose_bp.route('/api/pending_records', methods=['GET'])
@role_required(ROLE_ROOT, ROLE_ENGINEER, ROLE_PRODUCTION)
def api_pending_records():
    """
    待办列表：最新流转 NEXT_OWNER_ID = 当前用户（root 默认全量，可传 owner_id 过滤）。
    生产角色固定 owner_id = PRODUCTION_OP_ID。
    Query: product_id, keyword, page, page_size, owner_id(仅 root)
    """
    product_id = request.args.get('product_id', '').strip()
    keyword = request.args.get('keyword', '').strip()
    page = request.args.get('page', 1)
    page_size = request.args.get('page_size', 20)

    actor_user_id, _ = _actor()
    if is_root():
        owner_raw = request.args.get('owner_id', None)
        if owner_raw is None or str(owner_raw).strip() == '':
            owner_id = None
        else:
            try:
                owner_id = int(owner_raw)
            except (TypeError, ValueError):
                return jsonify({'code': 400, 'msg': 'owner_id 无效', 'data': [], 'total': 0}), 400
    elif is_production():
        from app.config import Config
        owner_id = int(getattr(Config, 'PRODUCTION_OP_ID', 181) or 181)
    else:
        owner_id = actor_user_id

    success, msg, payload = dispose_ctrl.get_pending_records(
        owner_id=owner_id,
        product_id=product_id,
        keyword=keyword,
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
    return jsonify({'code': 500, 'msg': msg, 'data': [], 'total': 0}), 500
