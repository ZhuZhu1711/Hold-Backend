from flask import Blueprint, render_template, request, jsonify, session
from app.controllers import product_ctrl
from app.utils.auth_decorators import (
    ROLE_FT_PRODUCT_MANAGER,
    ROLE_ROOT,
    current_role_name,
    role_required,
)

product_bp = Blueprint('product', __name__, url_prefix='/admin/products')

_PRODUCT_ADMIN = role_required(ROLE_ROOT, ROLE_FT_PRODUCT_MANAGER)


def _query_error(msg):
    if msg == '产品不存在':
        return jsonify({'code': 404, 'msg': msg, 'data': None}), 404
    if msg in ('产品ID不能为空', 'PIE 不能为空') or str(msg).startswith('PIE 不能超过'):
        return jsonify({'code': 400, 'msg': msg, 'data': None}), 400
    return jsonify({'code': 500, 'msg': msg, 'data': None}), 500

# ==========================================
# 页面路由
# ==========================================

@product_bp.route('')
@_PRODUCT_ADMIN
def product_list_page():
    """
    产品管理页面
    URL: /admin/products
    """
    return render_template(
        'products/list.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
    )

# ==========================================
# 数据接口路由
# ==========================================

@product_bp.route('/api', methods=['GET'])
@_PRODUCT_ADMIN
def get_products():
    """
    获取产品列表（支持搜索）
    URL: /admin/products/api?search=xxx
    """
    search = request.args.get('search', '')
    success, msg, products = product_ctrl.get_all_products(search)
    
    if success:
        return jsonify({
            'code': 200,
            'msg': msg,
            'data': product_ctrl.group_product_families(products),
        })
    return jsonify({'code': 500, 'msg': msg, 'data': []})


@product_bp.route('/api/by-product', methods=['GET'])
@_PRODUCT_ADMIN
def get_pie_by_product():
    """
    按产品型号查所属 PIE
    URL: /admin/products/api/by-product?product_id=GC1084-3.5
    """
    success, msg, data = product_ctrl.lookup_pie_by_product(
        request.args.get('product_id', ''),
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    return _query_error(msg)


@product_bp.route('/api/by-pie', methods=['GET'])
@_PRODUCT_ADMIN
def get_products_by_pie():
    """
    按 PIE 查全部产品
    URL: /admin/products/api/by-pie?pie=张三
    """
    success, msg, data = product_ctrl.lookup_products_by_pie(
        request.args.get('pie', ''),
    )
    if not success:
        return _query_error(msg)
    return jsonify({
        'code': 200,
        'msg': msg,
        'data': {
            'pie': data['pie'],
            'products': data['products'],
        },
    })

@product_bp.route('/api', methods=['POST'])
@_PRODUCT_ADMIN
def create_product():
    """
    新增产品
    URL: /admin/products/api
    """
    data = request.get_json()
    success, msg = product_ctrl.add_product(data)
    
    if success:
        return jsonify({'code': 200, 'msg': msg})
    else:
        return jsonify({'code': 400, 'msg': msg}), 400

@product_bp.route('/api/family', methods=['PUT'])
@_PRODUCT_ADMIN
def update_family():
    """
    按型号族更新工程师、PIE，以及各形态自己的 Gross Die
    URL: /admin/products/api/family
    """
    data = request.get_json() or {}
    success, msg = product_ctrl.update_family(data.get('family', ''), data)
    if success:
        return jsonify({'code': 200, 'msg': msg})
    status = 404 if msg == '产品不存在' else 400
    return jsonify({'code': status, 'msg': msg}), status


@product_bp.route('/api/<int:product_id>', methods=['PUT'])
@_PRODUCT_ADMIN
def update_product(product_id):
    """
    更新产品信息（GROSS_DIE、工程师、PIE）
    URL: /admin/products/api/1
    """
    data = request.get_json()
    success, msg = product_ctrl.update_product(product_id, data)
    
    if success:
        return jsonify({'code': 200, 'msg': msg})
    else:
        return jsonify({'code': 400, 'msg': msg}), 400