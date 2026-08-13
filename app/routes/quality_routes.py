"""
质量部工作台：只读物料处置报表（放行 / 降级 / 重测）。
"""
from flask import Blueprint, render_template, request, jsonify, session

from app.controllers import quality_ctrl
from app.utils.auth_decorators import quality_required, current_role_name
from app.utils.excel_export import stamp_filename, xlsx_or_error

quality_bp = Blueprint('quality', __name__, url_prefix='/qa')


def _page_ctx(**extra):
    ctx = {
        'user_name': session.get('user_name'),
        'role_name': current_role_name(),
    }
    ctx.update(extra)
    return ctx


@quality_bp.route('/')
@quality_bp.route('/dashboard')
@quality_required
def dashboard():
    """质量部首页即处置报表（该角色仅此功能）。"""
    return render_template('qa/disposes.html', **_page_ctx())


@quality_bp.route('/disposes')
@quality_required
def disposes_page():
    return render_template('qa/disposes.html', **_page_ctx())


@quality_bp.route('/api/disposes', methods=['GET'])
@quality_required
def api_quality_disposes():
    """
    已处置物料列表（只读，分页）。
    Query:
      start_dttm / end_dttm  处置时间
      product_id             型号（模糊）
      dispose                1放行 / 2降级 / 3重测，空=三类全部
      record_type            0=FT / 1=FVI / 2=WLT
      route                  ROUTE_ID（模糊）
      page / page_size
    """
    success, msg, payload = quality_ctrl.query_quality_disposes(
        start_dttm=request.args.get('start_dttm', '').strip(),
        end_dttm=request.args.get('end_dttm', '').strip(),
        product_id=request.args.get('product_id', '').strip(),
        dispose=request.args.get('dispose'),
        record_type=request.args.get('record_type'),
        route=request.args.get('route', '').strip(),
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
    status = 400 if any(k in msg for k in ('无效', '须为', '仅支持', '不能晚于')) else 500
    return jsonify({'code': status, 'msg': msg, 'data': [], 'total': 0}), status


@quality_bp.route('/api/disposes/export', methods=['GET'])
@quality_required
def api_quality_disposes_export():
    """
    导出已处置物料为 xlsx（筛选条件与列表一致，最多 5000 行）。
    Query: start_dttm, end_dttm, product_id, dispose, record_type, route
    """
    success, msg, content = quality_ctrl.export_quality_disposes_xlsx(
        start_dttm=request.args.get('start_dttm', '').strip(),
        end_dttm=request.args.get('end_dttm', '').strip(),
        product_id=request.args.get('product_id', '').strip(),
        dispose=request.args.get('dispose'),
        record_type=request.args.get('record_type'),
        route=request.args.get('route', '').strip(),
    )
    return xlsx_or_error(
        success, msg, content, stamp_filename('quality_disposes'),
        bad_keys=('无效', '须为', '仅支持', '不能晚于'),
    )


@quality_bp.route('/api/products', methods=['GET'])
@quality_required
def api_quality_products():
    keyword = request.args.get('keyword', '').strip()
    success, msg, data = quality_ctrl.get_quality_product_options(keyword)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    return jsonify({'code': 500, 'msg': msg, 'data': []}), 500


@quality_bp.route('/api/routes', methods=['GET'])
@quality_required
def api_quality_routes():
    keyword = request.args.get('keyword', '').strip()
    success, msg, data = quality_ctrl.get_quality_route_options(keyword)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    return jsonify({'code': 500, 'msg': msg, 'data': []}), 500


@quality_bp.route('/api/records/<int:record_id>', methods=['GET'])
@quality_required
def api_quality_record_detail(record_id):
    """只读：record 摘要 + 该单放行/降级/重测记录。"""
    success, msg, data = quality_ctrl.get_quality_record_detail(record_id)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    status = 404 if '不存在' in msg else (400 if '无效' in msg else 500)
    return jsonify({'code': status, 'msg': msg, 'data': None}), status
