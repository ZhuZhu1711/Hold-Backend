import os
import sys
from datetime import timedelta

class Config:

    SQLALCHEMY_DATABASE_URI = 'oracle+oracledb://FT_OWEN:Mee0MvpgXU!Lcp@172.18.202.5:1521/?service_name=jsqy'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Flask Session 签名密钥：必须跨进程/重启稳定，否则持久 Cookie 登录态会失效
    # 生产环境请通过环境变量 HOLD_SECRET_KEY 覆盖
    SQLALCHEMY_SECRET_KEY = os.environ.get(
        'HOLD_SECRET_KEY',
        'hold-backend-session-secret-change-in-production',
    )

    # Session + 持久 Cookie（自动登录）
    # 勾选「记住我」后 session.permanent=True，Cookie 带 Expires/Max-Age
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    SESSION_REFRESH_EACH_REQUEST = True  # 滑动续期：有请求则延长有效期
    SESSION_COOKIE_NAME = 'hold_session'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # HTTPS 部署时可设环境变量 HOLD_SESSION_SECURE=1
    SESSION_COOKIE_SECURE = os.environ.get('HOLD_SESSION_SECURE', '').lower() in (
        '1', 'true', 'yes',
    )

    WLT_TEST_DATA_REMOTE_PATH = '/WLT_TESTLOG/MAP_CP_PDF/'
    FT_TEST_DATA_REMOTE_PATH = '/FT_TESTLOG/'

    # Hold info 合并为 hold_record 的定时任务配置
    def _argv_is_debug_mode():
        for i, a in enumerate(sys.argv):
            a_l = a.lower()
            if a_l.startswith('--mode='):
                if a_l.split('=', 1)[1] == 'debug':
                    return True
            if a_l == '--mode':
                if i + 1 < len(sys.argv) and sys.argv[i + 1].lower() == 'debug':
                    return True
            if a_l in ('mode==debug', 'mode=debug', 'debug'):
                return True
        return False

    HOLD_INFO_TABLE = 'FT_HOLD_INFO_TEST' if _argv_is_debug_mode() else 'FT_HOLD_INFO'
    HOLD_RECORD_TABLE = 'FT_HOLD_RECORD'
    # 源表关联 hold_record 的字段（TEST=HOLD_RECORD_ID；正式表迁移后同步修改）
    HOLD_INFO_LINK_COLUMN = 'HOLD_RECORD_ID'
    HOLD_MERGE_INTERVAL_MINUTES = 30
    # MES 已解 hold 但 record 未关闭时，合并任务内自动关闭（每轮上限）
    HOLD_AUTO_CLOSE_ENABLED = True
    HOLD_AUTO_CLOSE_BATCH_SIZE = 200
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