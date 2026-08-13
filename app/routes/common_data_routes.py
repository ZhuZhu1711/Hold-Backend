"""
常规数据的接口(接口规范: /api/common_data/类别/item --> /api/common_data/product/gross_die)
产品类: 请求gross_die...
"""
from flask import Blueprint, request, jsonify
from app.controllers.common_data_ctrl import get_gross_die_value, get_ftp_status
from app.utils.auth_decorators import login_required
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text
from app import db


common_data_bp = Blueprint('common_data', __name__, url_prefix='/api/common_data')

# latest版本号查询接口
@common_data_bp.route('/software/latest_version', methods=['GET'])
def get_latest_version():
    try:
        version = db.session.execute(
            text("SELECT LATEST_VERSION FROM SOFTWARE_INFO")
        ).scalar()
        return jsonify({
            'code': 200,
            'msg': 'success',
            'data': {'version': version or "1.0.0"}
        })
    except Exception as e:
        return jsonify({
            'code': 500,
            'msg': f'查询失败: {str(e)}',
            'data': None
        }), 500


@common_data_bp.route('/ftp/status', methods=['GET'])
@login_required
def ftp_status():
    """
    数据分析用 testlog FTP 探活。
    登录后可调；FTP 挂了仍返回 200，data.available=false。
    """
    success, msg, data = get_ftp_status()
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    return jsonify({'code': 500, 'msg': msg, 'data': None}), 500


@common_data_bp.route('/product/gross_die', methods=['GET'])
def get_gross_die():
    product_id = request.args.get('product_id', '').strip()
    if not product_id:
        return jsonify({'code': 400, 'msg': '缺少参数 product_id', 'data': None}), 400

    try:
        success, result = get_gross_die_value(product_id)

        if success:
            return jsonify({
                'code': 200, 
                'msg': 'success', 
                'data': {'product_id': product_id, 'gross_die': result}
            })
        else:
            return jsonify({'code': 404, 'msg': result, 'data': None}), 404

    except SQLAlchemyError as e:
        return jsonify({'code': 500, 'msg': f'数据库查询异常: {str(e)}', 'data': None}), 500
    except Exception as e:
        return jsonify({'code': 500, 'msg': f'服务器内部错误: {str(e)}', 'data': None}), 500
    
    
    
    