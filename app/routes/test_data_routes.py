"""
请求测试数据的接口
"""
from flask import Blueprint, request, jsonify
from app.controllers import testlog_ctrl
from datetime import datetime


test_data_bp = Blueprint('test_data', __name__, url_prefix='/api/test_data')

TESTLOG_DIR = [
    '/FT_TESTLOG/',
    '/WLT_TESTLOG/'
]

def is_valid_date_format(date_string):
    try:
        date_obj = datetime.strptime(date_string, "%Y-%-m-%-d")
        return True
    except ValueError:
        return False

@test_data_bp.route('/bysite', methods=['GET'])
def get_bysite_data():
    wafer_id = request.args.get('wafer_id', '').strip()
    # date_str = request.args.get('date', '').strip()
    step_str = request.args.get('step', '').strip()                             # ATE, WLT

    if not wafer_id:
        return jsonify({"error": "wafer_id is required"}), 400

    # if not is_valid_date_format(date_str):
    #     return jsonify({"error": "invalid date string"}), 400
    
    if step_str not in ['ATE', 'WLT']:
        return jsonify({"error": "invalid step param.Must in ATE | WLT"}), 400
    step_list = ['FA'] if step_str == 'ATE' else ['WLTA', 'WLTB']
    resp = testlog_ctrl.get_testlog_bysite_str(wafer_id, step_list)
    if resp is not None:
        return jsonify({'code': 200, 'bysite': resp, 'msg': 'bysite获取成功'})
    else:
        return jsonify({'code': 500, 'bysite': resp, 'msg': 'bysite获取失败'})


@test_data_bp.route('/ftp_path', methods=['GET'])
def get_ftp_path():
    """
    按 product_id + wafer_id + step 查询 FT_WLT_TESTLOG 的 FTP_PATH 列表（含 test_date，按日期倒序）

    Query:
        product_id: 产品 ID（必填）
        wafer_id: 晶圆 ID（必填）
        step: 工步类型，仅允许 ATE | WLT（必填）
              ATE → STEP = 'FA'
              WLT → STEP LIKE 'WLT_'
    """
    product_id = request.args.get('product_id', '').strip()
    wafer_id = request.args.get('wafer_id', '').strip()
    step = request.args.get('step', '').strip()

    if not product_id:
        return jsonify({'code': 400, 'msg': '缺少参数 product_id', 'data': []}), 400
    if not wafer_id:
        return jsonify({'code': 400, 'msg': '缺少参数 wafer_id', 'data': []}), 400
    if not step:
        return jsonify({'code': 400, 'msg': '缺少参数 step', 'data': []}), 400
    if step not in ['ATE', 'WLT']:
        return jsonify({'code': 400, 'msg': 'invalid step param. Must in ATE | WLT', 'data': []}), 400

    success, msg, data = testlog_ctrl.get_ftp_paths(product_id, wafer_id, step)
    if success:
        return jsonify({'code': 200, 'msg': msg, 'data': data})
    return jsonify({'code': 500, 'msg': msg, 'data': []}), 500