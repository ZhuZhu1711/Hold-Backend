import oracledb
import os

class Config:

    SQLALCHEMY_DATABASE_URI = 'oracle+oracledb://FT_OWEN:Mee0MvpgXU!Lcp@172.18.202.5:1521/?service_name=jsqy'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_SECRET_KEY = os.urandom(24)

    WLT_TEST_DATA_REMOTE_PATH = '/WLT_TESTLOG/MAP_CP_PDF/'
    FT_TEST_DATA_REMOTE_PATH = '/FT_TESTLOG/'

    # Hold info 合并为 hold_record 的定时任务配置
    HOLD_INFO_TABLE = 'FT_HOLD_INFO_TEST'
    HOLD_RECORD_TABLE = 'FT_HOLD_RECORD'
    HOLD_MERGE_INTERVAL_MINUTES = 30
    # 写入 FT_HOLD_RECORD 时的默认值（表字段 NOT NULL）
    HOLD_RECORD_TYPE = 0
    HOLD_RECORD_STATUS = 0
    # 同 wafer + station + hold_code 且 HOLD_DTTM 相差在该小时数内 → 视为重复
    HOLD_DEDUP_WINDOW_HOURS = 1
    # 仅转换下列白名单内的记录；HOLD_CODE 与 STATION 各自独立，无绑定关系
    # 需同时满足：HOLD_CODE ∈ 列表 且 STATION ∈ 列表；任一侧为空则不转换
    HOLD_MERGE_HOLD_CODES = [
        '023', '024', '027',             # 良率
        '025',                           # 缺陷率
        '026',                           # 工程品
        '028'                            # 重码
    ]
    HOLD_MERGE_STATIONS = [
        'FATE-FA',
        'FAOIFINISH',
        'FFVI',
        'FIQC_MERGE',
        'FPQC',
        'FIQC FUNCTION TEST',
        'FIQC WG TEST'
    ]
