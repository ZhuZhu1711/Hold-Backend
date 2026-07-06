"""
晶圆测试数据查询控制器
"""
from app import db
from sqlalchemy import desc
from app.models.rawdata import TestWafer, TestBincode
import json



def get_wafer_yield_and_bin(wafer_id, operation_id):
    """
    根据 wafer_id 和 operation_id 查询最新一条测试记录
    返回良率和BIN码比率
    
    Args:
        wafer_id: 晶圆ID
        operation_id: 工序ID
    
    Returns:
        dict: 包含良率和BIN码比率的数据
    """
    # 1. 查询最新一条 wafer 记录（ID最大）
    wafer = (
        TestWafer.query
        .filter(
            TestWafer.WAFER_ID == wafer_id,
            TestWafer.OPERATION_ID == operation_id
        )
        .order_by(desc(TestWafer.ID))
        .first()
    )
    
    if not wafer:
        return None
    
    # 2. 计算良率：从 GRADES_QTY 中统计含'A'的等级数量 / GROSS_DIE
    yield_result = _calculate_yield(wafer)
    
    # 3. 查询 BIN码数据
    bincodes = (
        TestBincode.query
        .filter(TestBincode.TEST_WAFER_SEQ == wafer.ID)
        .all()
    )
    
    # 4. 计算BIN码比率
    bin_ratio = _calculate_bin_ratio(bincodes)
    
    # 5. 组装返回数据
    return {
        'wafer_id': wafer.WAFER_ID,
        'operation_id': wafer.OPERATION_ID,
        'ft_time': str(wafer.FT_TIME) if wafer.FT_TIME else None,
        'product_id': wafer.PRODUCT_ID,
        'yield': yield_result,
        'bin_ratio': bin_ratio
    }


def _calculate_yield(wafer):
    """
    计算良率
    从 GRADES_QTY JSON中，把所有含字母'A'的等级数量求和，除以 GROSS_DIE
    
    Args:
        wafer: TestWafer 对象
    
    Returns:
        dict: 良率计算详情
    """
    if not wafer.GRADES_QTY or not wafer.GROSS_DIE or wafer.GROSS_DIE == 0:
        return {
            'yield_rate': 0.0,
            'pass_die': 0,
            'gross_die': wafer.GROSS_DIE or 0
        }
    
    try:
        grades = json.loads(wafer.GRADES_QTY)
    except (json.JSONDecodeError, TypeError):
        return {
            'yield_rate': 0.0,
            'pass_die': 0,
            'gross_die': wafer.GROSS_DIE
        }
    
    # 统计含'A'的等级数量
    pass_die = sum(qty for grade, qty in grades.items() if 'A' in grade.upper())
    
    # 计算良率百分比，保留2位小数
    yield_rate = round(pass_die / wafer.GROSS_DIE * 100, 2)
    
    return {
        'yield_rate': yield_rate,
        'pass_die': pass_die,
        'gross_die': wafer.GROSS_DIE
    }


def _calculate_bin_ratio(bincodes):
    """
    计算BIN码比率
    每个BIN_CODE的数量占总数量的百分比
    
    Args:
        bincodes: TestBincode 对象列表
    
    Returns:
        dict: BIN码比率，key为BIN_CODE，value为百分比
    """
    if not bincodes:
        return {}
    
    # 计算总数量
    total_qty = sum(b.BIN_CODE_QTY or 0 for b in bincodes)
    
    if total_qty == 0:
        return {}
    
    # 计算每个BIN的比率
    bin_ratio = {}
    for b in bincodes:
        if b.BIN_CODE is not None:
            qty = b.BIN_CODE_QTY or 0
            ratio = round(qty / total_qty * 100, 2)
            bin_ratio[str(b.BIN_CODE)] = ratio
    
    return bin_ratio