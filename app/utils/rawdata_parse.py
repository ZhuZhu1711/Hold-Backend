"""
ATE/WLT/VBOX raw CSV → TEST_WAFER / TEST_BINCODE 内存结构。
逻辑来自 RAW_DATA_SCRIPT/data_object.py + util.py。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)

dttm_fmt1 = r'^\d{4}-\d{2}-\d{2} \d{2}\.\d{2}(?:\.\d{2})?$'
dttm_fmt2 = r'^\d{4}/\d{1,2}/\d{1,2} \d{1,2}:\d{2}(?::\d{2})?$'


@dataclass
class TEST_BINCODE:
    wafer_id: str
    bin_code: int
    bin_code_qty: int
    test_wafer_seq: int
    grade: str
    defect: str


@dataclass
class TEST_WAFER:
    id: Optional[int]
    wafer_id: Optional[str]
    operation_id: Optional[str]
    ft_time: Optional[datetime]
    product_id: Optional[str]
    second_code: Optional[str]
    test_program: Optional[str]
    rcv_time: Optional[datetime]
    lot_id: Optional[str]
    wafer_no: Optional[int]
    location: Optional[str]
    gross_die: Optional[int]
    wafer_num: Optional[int]
    route: Optional[str]
    equip_id: Optional[str]
    ass_vender: Optional[str]
    pack_lotid: Optional[str]
    pass_die: Optional[int]
    ng_num: Optional[int]
    cp: Optional[int]
    record_time: Optional[datetime]
    grades_qty: Optional[dict]
    bincodes: dict[int, TEST_BINCODE]


# CSV 列索引（0-based）
WAFER_ID = 0
OPERATION_ID = 1
FT_TIME = 2
PRODUCT_ID = 3
SECOND_CODE = 4
TEST_PROGRAM = 5
RCV_TIME = 6
LOT_ID = 7
WAFER_NO = 8
LOCATION = 9
GROSS_DIE = 10
WAFER_NUM = 11
ROUTE = 12
EQUIP_ID = 13
ASS_VENDER = 14
PACK_LOTID = 15
PASS_DIE = 16
NG_NUM = 17
CP = 18
BIN_CODE = 19
BIN_CODE_QTY = 20


def convert_str_to_datetime(str_dttm: str):
    if not isinstance(str_dttm, str):
        raise TypeError("输入必须是字符串")

    s = str_dttm.strip()

    if re.match(dttm_fmt1, s):
        date_part, time_part = s.split(' ', 1)
        date_part = date_part.replace('-', '/')
        time_part = time_part.replace('.', ':')
        cleaned = f"{date_part} {time_part}"
    elif re.match(dttm_fmt2, s):
        cleaned = s
    elif s == '':
        cleaned = "2000-01-01 00:00:00"
    else:
        cleaned = s

    try:
        return dateutil_parser.parse(cleaned)
    except Exception as e:
        raise ValueError(f"无法解析时间字符串: '{str_dttm}'") from e


def safe_int_convert(s):
    try:
        return int(float(str(s).strip()))
    except Exception:
        return None


def complete_product_id(product_id: str):
    if product_id.endswith('3'):
        return f"{product_id}.5"
    if product_id.endswith('2'):
        return f"{product_id}.6"
    return product_id


def get_file_type(fname: str):
    upper_path = fname.upper()
    if upper_path.startswith('ATE_'):
        return 'ATE'
    if upper_path.startswith('WLT_'):
        return 'WLT'
    if upper_path.startswith('ATEVBOX_'):
        return 'VBOX'
    return 'other'


def parse_csv_lines(file_type: str, lines: list):
    # file_type 保留与原脚本一致的签名（历史 FA/WLT 过滤已去掉）
    _ = file_type
    test_wafers: dict[str, TEST_WAFER] = dict()

    for i, line in enumerate(lines):
        try:
            wafer_id = line[WAFER_ID].strip()
            operation_id = line[OPERATION_ID].strip()
            wafer_flag = f"{wafer_id}-{operation_id}"
            bincode: str = line[BIN_CODE].strip()

            if operation_id == 'FATE-FT':
                operation_id = 'FT'
            elif operation_id == 'FATE-RT':
                operation_id = 'RT'

            if wafer_flag not in test_wafers:
                test_wafer = TEST_WAFER(
                    0,
                    wafer_id,
                    operation_id,
                    convert_str_to_datetime(line[FT_TIME]),
                    complete_product_id(line[PRODUCT_ID]),
                    line[SECOND_CODE],
                    line[TEST_PROGRAM],
                    convert_str_to_datetime(line[RCV_TIME]),
                    line[LOT_ID],
                    safe_int_convert(line[WAFER_NO]),
                    line[LOCATION],
                    safe_int_convert(line[GROSS_DIE]),
                    safe_int_convert(line[WAFER_NUM]),
                    line[ROUTE],
                    line[EQUIP_ID],
                    line[ASS_VENDER],
                    line[PACK_LOTID],
                    safe_int_convert(line[PASS_DIE]),
                    safe_int_convert(line[NG_NUM]),
                    safe_int_convert(line[CP]),
                    datetime.now(),
                    dict(),
                    dict(),
                )
                test_wafers[wafer_flag] = test_wafer
            else:
                test_wafer = test_wafers[wafer_flag]

            if bincode.isdigit():
                bc = TEST_BINCODE(
                    wafer_id,
                    int(bincode),
                    int(line[BIN_CODE_QTY]),
                    0,
                    line[21],
                    line[22],
                )
                if int(bincode) in test_wafer.bincodes:
                    test_wafer.bincodes[int(bincode)].bin_code_qty += bc.bin_code_qty
                else:
                    test_wafer.bincodes[int(bincode)] = bc
            else:
                grade = bincode
                qty = int(line[BIN_CODE_QTY])
                test_wafer.grades_qty[grade] = qty
        except Exception as e:
            logger.warning("异常发生在第%s行:异常信息:%s", i, e)
            continue

    return test_wafers
