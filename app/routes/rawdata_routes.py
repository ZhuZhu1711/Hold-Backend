"""
晶圆测试数据查询路由
"""
from flask import Blueprint, request, jsonify
from app.controllers.rawdata_ctrl import (
    get_latest_defect_bincodes,
    get_wafer_yield_and_bin,
)

rawdata_bp = Blueprint('rawdata', __name__, url_prefix='/api/raw_data')


@rawdata_bp.route('/yield', methods=['GET'])
def query_wafer_yield():
    """
    查询晶圆良率和BIN码比率
    
    Query参数:
        wafer_id: 晶圆ID (必填)
        operation_id: 工序ID (必填)
    
    Returns:
        JSON格式的良率和BIN比率数据
    """
    wafer_id = request.args.get('wafer_id')
    operation_id = request.args.get('operation_id')
    
    # 参数校验
    if not wafer_id:
        return jsonify({'code': 400, 'message': '缺少参数 wafer_id'}), 400
    if not operation_id:
        return jsonify({'code': 400, 'message': '缺少参数 operation_id'}), 400
    
    # 查询数据
    result = get_wafer_yield_and_bin(wafer_id, operation_id)
    
    if result is None:
        return jsonify({
            'code': 404,
            'message': f'未找到 wafer_id={wafer_id}, operation_id={operation_id} 的测试记录'
        }), 404
    
    return jsonify({
        'code': 200,
        'message': 'success',
        'data': result
    }), 200


@rawdata_bp.route('/defect_bincode', methods=['GET'])
def query_latest_defect_bincode():
    """
    查询最新一次测试的缺陷 BIN_CODE / BIN_CODE_QTY。

    Query:
        wafer_id      必填
        operation_id  必填（如 FATE-FA）
    """
    wafer_id = request.args.get('wafer_id', '').strip()
    operation_id = request.args.get('operation_id', '').strip()

    success, msg, data = get_latest_defect_bincodes(wafer_id, operation_id)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data}), 200

    status = 400 if '请指定' in msg else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status
