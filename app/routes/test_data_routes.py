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
    resp = testlog_ctrl.get_testlog_data_by_wafer(wafer_id, step_list)
    if resp is not None:
        return jsonify({'code': 200, 'bysite': resp, 'msg': 'bysite获取成功'})
    else:
        return jsonify({'code': 500, 'bysite': resp, 'msg': 'bysite获取失败'})