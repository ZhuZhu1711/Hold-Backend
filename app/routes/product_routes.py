from flask import Blueprint, render_template, request, jsonify, session, flash, redirect, url_for
from app.controllers import product_ctrl
from app.utils.auth_decorators import current_role_name
from functools import wraps

product_bp = Blueprint('product', __name__, url_prefix='/admin/products')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 检查 session 中是否有 user_id
        if not session.get('user_id'):
            flash('请先登录', 'warning') # 可选：显示提示信息
            return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# 页面路由
# ==========================================

@product_bp.route('')
@login_required
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
def get_products():
    """
    获取产品列表（支持搜索）
    URL: /admin/products/api?search=xxx
    """
    search = request.args.get('search', '')
    success, msg, products = product_ctrl.get_all_products(search)
    
    if success:
        product_list = []
        for p in products:
            # 处理关联的工程师姓名，如果没有工程师则显示 "未分配"
            engineer_name = p.owner.NAME if p.owner else "未分配"
            
            product_list.append({
                'id': p.ID,
                'product_id': p.PRODUCT_ID,
                'gross_die': p.GROSS_DIE,
                'line_type': p.LINE_TYPE,
                'update_dt': p.UPDATE_DTTM.strftime('%Y-%m-%d') if p.UPDATE_DTTM else '-',
                'engineer_id': p.PRO_ENG_ID,
                'engineer_name': engineer_name
            })
        return jsonify({'code': 200, 'msg': msg, 'data': product_list})
    else:
        return jsonify({'code': 500, 'msg': msg, 'data': []})

@product_bp.route('/api', methods=['POST'])
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

@product_bp.route('/api/<int:product_id>', methods=['PUT'])
def update_product(product_id):
    """
    更新产品信息（仅限 GROSS_DIE 和 工程师）
    URL: /admin/products/api/1
    """
    data = request.get_json()
    success, msg = product_ctrl.update_product(product_id, data)
    
    if success:
        return jsonify({'code': 200, 'msg': msg})
    else:
        return jsonify({'code': 400, 'msg': msg}), 400