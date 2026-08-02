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
    # 源表关联 hold_record 的字段（TEST=HOLD_RECORD_ID；正式表迁移后同步修改）
    HOLD_INFO_LINK_COLUMN = 'HOLD_RECORD_ID'
    HOLD_MERGE_INTERVAL_MINUTES = 30
    # 写入 FT_HOLD_RECORD 时 STATUS 默认值（表字段 NOT NULL）
    # RECORD_TYPE 由 dispose_api.md「处置单划分」按 PRODUCT_ID/HOLD_CODE/STATION 判定：
    #   0=FT异常反馈单  1=FVI异常反馈单  2=WLT异常反馈单；不满足规则则不转换
    HOLD_RECORD_STATUS = 0
    # 生产 OP 用户 ID（CIRCULATION_HISTORY 流转目标，见 dispose_api.md）
    PRODUCTION_OP_ID = 181
    # 系统/root 用户 ID
    SYSTEM_USER_ID = 1
    # 同 wafer + station + hold_code 且 HOLD_DTTM 相差在该小时数内 → 视为重复
    HOLD_DEDUP_WINDOW_HOURS = 1
