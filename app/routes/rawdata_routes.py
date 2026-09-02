"""
晶圆测试数据查询路由
"""
from flask import Blueprint, request, jsonify
from app.controllers.rawdata_ctrl import (
    get_latest_defect_bincodes,
    get_mes_defect_bin_qty,
    get_wafer_yield_and_bin,
)

rawdata_bp = Blueprint('rawdata', __name__, url_prefix='/api/raw_data')


@rawdata_bp.route('/yield', methods=['GET'])
def query_wafer_yield():
    """
    查询晶圆良率和BIN码比率
    
    Query参数:
        wafer_id: 晶圆ID (必填)
        operation_id: 工序ID (必填；FA / FATE-FA 同时命中，不支持 RT/FT)
    
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
        operation_id  必填（FATE-FA / VBOX-FA；传入 FA / FATE-FA 时同时命中两种写法，不支持 RT/FT）
    """
    wafer_id = request.args.get('wafer_id', '').strip()
    operation_id = request.args.get('operation_id', '').strip()

    success, msg, data = get_latest_defect_bincodes(wafer_id, operation_id)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data}), 200

    status = 400 if '请指定' in msg else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


@rawdata_bp.route('/mes_defect_bin', methods=['GET'])
def query_mes_defect_bin():
    """
    从 MES 查询缺陷 BIN 数量（仅 DEFECT_CODE + QTY，BIN_NAME 默认 F）。
    按 code 去重；has_duplicate / duplicate_codes 标明是否出现过重复。

    Query:
        lot_id     必填（MES LOT_ID，如 C200161-027）
        line_type  可选，默认 FT
        bin_name   可选，默认 F
    """
    lot_id = request.args.get('lot_id', '').strip()
    line_type = request.args.get('line_type', 'FT').strip() or 'FT'
    bin_name = request.args.get('bin_name', 'F').strip() or 'F'

    success, msg, data = get_mes_defect_bin_qty(
        lot_id, line_type=line_type, bin_name=bin_name,
    )
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data}), 200

    status = 400 if '请指定' in msg else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status
