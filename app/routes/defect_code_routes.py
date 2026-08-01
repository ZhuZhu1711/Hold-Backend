from flask import Blueprint, request, jsonify, render_template, session
from app.controllers.defect_code_ctrl import (
    get_defects_by_product,
    create_defect,
    delete_defect,
    update_grade
)
from app.utils.auth_decorators import current_role_name

defect_bp = Blueprint('defect', __name__, url_prefix='/admin/defects')

@defect_bp.route('')
def defect_code_list_page():
    return render_template(
        'defect/list.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
        product_id=request.args.get('product_id', ''),
    )

@defect_bp.route('/api', methods=['GET'])
def get_defects():
    """API: 获取产品对应的缺陷列表 (通过查询参数 ?product_id=...)"""
    product_id = request.args.get('product_id')
    
    # 3. 参数校验
    if not product_id:
        return jsonify({'error': '缺少参数 product_id'}), 400
    
    defects = get_defects_by_product(product_id)
    
    return jsonify([d.to_dict() if hasattr(d, 'to_dict') else d for d in defects])

@defect_bp.route('/defects/add', methods=['POST'])
def add_defect():
    """API: 新增缺陷"""
    data = request.get_json()
    new_defect = create_defect(data)
    return jsonify(new_defect.to_dict()), 201

@defect_bp.route('/defects/<int:defect_id>', methods=['DELETE'])
def remove_defect(defect_id):
    """API: 删除缺陷"""
    defect = delete_defect(defect_id)
    return jsonify({'status': 'success'} if defect else {'status': 'not found'}), 200

@defect_bp.route('/defects/<int:defect_id>/grade', methods=['PUT'])
def change_grade(defect_id):
    """API: 修改等级"""
    new_grade = request.json.get('grade')
    defect = update_grade(defect_id, new_grade)
    return jsonify(defect.to_dict() if defect else {'error': 'not found'}), 200
