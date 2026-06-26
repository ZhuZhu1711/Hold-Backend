"""
常规数据的接口(接口规范: /api/common_data/类别/item --> /api/common_data/product/gross_die)
产品类: 请求gross_die...
"""
from flask import Blueprint, request, jsonify
from app.controllers.common_data_ctrl import get_gross_die as get_gross_die_value


common_data_bp = Blueprint('common_data', __name__, url_prefix='/api/common_data')


@common_data_bp.route('/product/gross_die')
def get_gross_die():
    product_id = request.args.get('product_id', '').strip()
    if not product_id:
        return jsonify({'code': 400, 'msg': '缺少参数 product_id', 'data': None}), 400

    gross_die = get_gross_die_value(product_id)
    if gross_die is None:
        return jsonify({'code': 404, 'msg': '未找到对应产品', 'data': None}), 404

    return jsonify({'code': 200, 'msg': 'success', 'data': {'product_id': product_id, 'gross_die': gross_die}})
    