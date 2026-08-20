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
    # 客户端「打开后台」一次性票据有效期（秒）
    WEB_SSO_TICKET_MAX_AGE = 60
    # 外部系统固定 Token（Header: X-Hold-Token）。空字符串 = 关闭该通道。
    # 生产请用环境变量 HOLD_API_TOKEN，勿把真实值提交进仓库。
    HOLD_API_TOKEN = os.environ.get('HOLD_API_TOKEN', '').strip()

    WLT_TEST_DATA_REMOTE_PATH = '/WLT_TESTLOG/MAP_CP_PDF/'
    FT_TEST_DATA_REMOTE_PATH = '/FT_TESTLOG/'
    # 手提 Hold 附件图 FTP（独立于 TESTLOG；请自行填写 HOST / USER / PASSWD）
    ANNEX_FTP_HOST = '172.18.107.206'
    ANNEX_FTP_USER = 'ft'
    ANNEX_FTP_PASSWD = 'FTabc@123'
    ANNEX_FTP_TIMEOUT = 8
    ANNEX_FTP_FT_DIR = '/JDY_UPLOAD/FT_MANUAL/'
    ANNEX_FTP_WLT_DIR = '/JDY_UPLOAD/WLT_MANUAL/'

    # 数据分析用 testlog FTP（bysite / testlog 下载）
    TESTLOG_FTP_HOST = '172.18.200.250'
    TESTLOG_FTP_USER = 'share'
    TESTLOG_FTP_PASSWD = 'abc@123'
    TESTLOG_FTP_TIMEOUT = 8

    # Raw data CSV ingest（独立进程 app/backend_schedule/FT_RAW_DATA_sche.py，不挂 Web）
    RAW_DATA_FTP_HOST = '172.18.107.206'
    RAW_DATA_FTP_USER = 'ft'
    RAW_DATA_FTP_PASSWD = 'FTabc@123'
    RAW_DATA_FTP_TIMEOUT = 60
    RAW_DATA_REMOTE_DIR = '/RAW_DATA/'
    RAW_DATA_BAK_DIR = '/RAW_DATA_BAK'
    RAW_DATA_LOCAL_DIR = 'RAW_DATA'
    RAW_DATA_INTERVAL_MINUTES = 60

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
    HOLD_RECORD_TABLE = 'FT_HOLD_RECORD_TEST' if _argv_is_debug_mode() else 'FT_HOLD_RECORD'
    CIRCULATION_HISTORY_TABLE = (
        'CIRCULATION_HISTORY_TEST' if _argv_is_debug_mode() else 'CIRCULATION_HISTORY'
    )
    HOLD_PREDICT_TABLE = 'FT_HOLD_PREDICT_TEST' if _argv_is_debug_mode() else 'FT_HOLD_PREDICT'
    # 过渡期：新系统处置成功后静默写回旧 HOLD_INFO / WLT_HOLD_INFO / HISTORY_DISPOSITION。
    # 旧系统完全下线后把下面改成 False（重启后端即停写，无需删代码）。
    # 也可不改代码：环境变量 HOLD_LEGACY_WRITEBACK=0 后重启。
    # debug 模式始终关闭，避免测试处置写入正式旧表。
    LEGACY_DISPOSE_WRITEBACK = True
    LEGACY_DISPOSE_WRITEBACK_ENABLED = (
        LEGACY_DISPOSE_WRITEBACK
        and (not _argv_is_debug_mode())
        and os.environ.get('HOLD_LEGACY_WRITEBACK', '1').strip().lower()
        not in ('0', 'false', 'no', 'off')
    )
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

    # FT 可放行概率静默打分（独立调度，不改处置/UI）。
    # 代码保留；改成 True 并重启后端即可重新启用。独立脚本 FT_HOLD_PREDICT_sche.py 同样看此开关。
    HOLD_PREDICT_ENABLED = False
    HOLD_PREDICT_INTERVAL_MINUTES = 15
    HOLD_PREDICT_WAIT_HOURS = 24
    HOLD_PREDICT_BATCH_SIZE = 40
    HOLD_PREDICT_LABEL_BATCH_SIZE = 200
    def _resource_root():
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    HOLD_PREDICT_MODEL_PATH = os.path.join(
        _resource_root(),
        'app', 'hold_predict', 'artifacts', 'model_v1.joblib',
    )

    HOLD_MERGE_HOLD_CODES = [
        '023', '024', '027',             # 良率
        '025',                           # 缺陷率
        '026',                           # 工程品
        '028',                           # 重码
        'AQL_HOLD',                      # AQL / 手提 FT
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

    # 严重报错邮件（SMTP 用户名+密码登录）。普通 logger.error / 业务异常不发送。
    # 未配齐 SMTP_HOST / USER / PASSWORD / TO 时静默跳过。
    ALERT_MAIL_ENABLED = True
    ALERT_SMTP_HOST = ''
    ALERT_SMTP_PORT = 465
    ALERT_SMTP_SSL = True          # True=SMTP_SSL(465)；False=先连明文再 STARTTLS(587)
    ALERT_SMTP_USER = ''
    ALERT_SMTP_PASSWORD = ''
    ALERT_MAIL_FROM = ''           # 空则用 ALERT_SMTP_USER
    ALERT_MAIL_TO = []             # 收件人列表，如 ['ops@example.com']
    ALERT_MAIL_COOLDOWN_SECONDS = 1800  # 相同严重错误冷却，避免刷屏