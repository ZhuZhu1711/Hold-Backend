"""
晶圆测试数据查询控制器
"""
from app import db
from sqlalchemy import desc, text
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


def get_latest_defect_bincodes(wafer_id, operation_id):
    """
    查询指定 wafer 在某工序下最新一次测试的缺陷 BIN_CODE / BIN_CODE_QTY。

    Args:
        wafer_id: 晶圆 ID
        operation_id: 工序 ID（如 FATE-FA）

    Returns:
        (True, msg, data) 或 (False, msg, None)
        data: {bin_code: bin_code_qty, ...}
    """
    if wafer_id is None or not str(wafer_id).strip():
        return False, '请指定 wafer_id', None
    if operation_id is None or not str(operation_id).strip():
        return False, '请指定 operation_id', None

    wafer_id = str(wafer_id).strip()
    operation_id = str(operation_id).strip()

    sql = """
        SELECT
            atb.BIN_CODE,
            atb.BIN_CODE_QTY
        FROM TEST_BINCODE atb
        INNER JOIN (
            SELECT atw.id, atw.product_id
            FROM TEST_WAFER atw
            WHERE atw.WAFER_ID = :wafer_id
              AND atw.operation_id = :operation_id
            ORDER BY atw.id DESC
            FETCH FIRST 1 ROW ONLY
        ) latest_wafer ON atb.TEST_WAFER_SEQ = latest_wafer.id
        INNER JOIN PRODUCT_INFO pi2 ON latest_wafer.product_id = pi2.product_id
        GROUP BY
            atb.BIN_CODE,
            atb.BIN_CODE_QTY
        ORDER BY atb.BIN_CODE_QTY DESC NULLS LAST, atb.bin_code
    """

    try:
        rows = db.session.execute(
            text(sql),
            {'wafer_id': wafer_id, 'operation_id': operation_id},
        ).fetchall()
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', None

    # 按 qty 从高到低插入，保留顺序（Py3.7+ dict）
    data = {}
    for bin_code, bin_code_qty in rows:
        if bin_code is None:
            continue
        data[str(int(bin_code))] = int(bin_code_qty) if bin_code_qty is not None else 0

    return True, '获取成功', data



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
