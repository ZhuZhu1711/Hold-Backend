import os
import sys
from datetime import timedelta


# 服务器环境。不设置视为测试。正式机必须设 HOLD_ENV=release 才能启动正式库和正式端口。
# 空 / test / debug → 测试；只有 release → 正式。其它值拒绝启动，避免写错库。
_TEST_ENV_VALUES = frozenset({'', 'test', 'debug'})
_RELEASE_ENV_VALUE = 'release'


def hold_env(environ=None) -> str:
    """返回 'test' 或 'release'。"""
    environ = os.environ if environ is None else environ
    raw = (environ.get('HOLD_ENV') or '').strip().lower()
    if raw in _TEST_ENV_VALUES:
        return 'test'
    if raw == _RELEASE_ENV_VALUE:
        return 'release'
    raise SystemExit(
        f'HOLD_ENV 未设置时为测试。正式环境必须设 HOLD_ENV=release，当前为 {raw!r}，已拒绝启动。'
    )


def is_test_env(environ=None) -> bool:
    return hold_env(environ) != 'release'


def cp_hold_tables(test_env: bool = False) -> tuple:
    """CP 处置单和流转表。两个库的表名相同，靠连接分开。"""
    del test_env
    return 'CP_HOLD_RECORD', 'CP_CIRCULATION_HISTORY'


def reject_legacy_mode_arg(argv=None):
    """启动参数 --mode 已停用，避免和 HOLD_ENV 各说各话。"""
    argv = sys.argv if argv is None else argv
    for arg in argv[1:]:
        if arg == '--mode' or str(arg).lower().startswith('--mode='):
            raise SystemExit(
                '不再使用 --mode。测试不设置 HOLD_ENV；正式环境必须设 HOLD_ENV=release。'
            )


def hold_api_token_env_name(environ=None):
    """正式: HOLD_API_TOKEN；测试: HOLD_API_TOKEN_DEBUG。"""
    return 'HOLD_API_TOKEN_DEBUG' if is_test_env(environ) else 'HOLD_API_TOKEN'


def load_hold_api_token(environ=None):
    environ = os.environ if environ is None else environ
    name = hold_api_token_env_name(environ)
    return name, (environ.get(name) or '').strip()


# 业务库。测试和正式并排放，改地址或账号只改对应这一段。
# 不设置 HOLD_ENV 用 test；HOLD_ENV=release 用 release。
DB_PROFILES = {
    'test': {
        'user': 'fc_admin',
        'password': 'Fca3GvpcKU!BTP',
        'host': '172.18.252.121',
        'port': 1521,
        'service_name': 'mestdb',
    },
    'release': {
        'user': 'FT_OWEN',
        'password': 'Mee0MvpgXU!Lcp',
        'host': '172.18.202.5',
        'port': 1521,
        'service_name': 'jsqy',
    },
}


def db_profile(environ=None) -> dict:
    key = 'test' if is_test_env(environ) else 'release'
    return DB_PROFILES[key]


def oracle_dsn(profile) -> str:
    return f"{profile['host']}:{profile['port']}/{profile['service_name']}"


def oracle_sqlalchemy_uri(profile) -> str:
    return (
        f"oracle+oracledb://{profile['user']}:{profile['password']}"
        f"@{profile['host']}:{profile['port']}/?service_name={profile['service_name']}"
    )


class Config:

    _db = db_profile()
    DB_USER = _db['user']
    DB_PASSWORD = _db['password']
    DB_DSN = oracle_dsn(_db)
    SQLALCHEMY_DATABASE_URI = oracle_sqlalchemy_uri(_db)
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
    # 正式读 HOLD_API_TOKEN，测试（HOLD_ENV=test）读 HOLD_API_TOKEN_DEBUG。勿把真实值提交进仓库。
    HOLD_API_TOKEN_ENV, HOLD_API_TOKEN = load_hold_api_token()

    # NCMR 处置单批量导入。网页和客户端都经本服务转发。
    # 默认用固定账号登录，token 只留在进程内存，不进客户端、不进日志。
    # HOLD_NCMR_IMPORT_URL / HOLD_NCMR_LOGIN_URL / HOLD_NCMR_USERNAME /
    # HOLD_NCMR_PASSWORD 可覆盖。HOLD_NCMR_TOKEN 若显式设置则跳过登录。
    NCMR_IMPORT_URL = os.environ.get(
        'HOLD_NCMR_IMPORT_URL',
        'http://172.19.15.11/ncmr-api/factory/v1/disposalOrder/batchImport',
    )
    NCMR_LOGIN_URL = os.environ.get(
        'HOLD_NCMR_LOGIN_URL',
        'http://172.19.15.11/user-center-api/login',
    )
    NCMR_USERNAME = os.environ.get('HOLD_NCMR_USERNAME', 'dongxian_zhu')
    NCMR_PASSWORD = os.environ.get(
        'HOLD_NCMR_PASSWORD',
        'vJMtrdKSCbjkn5E4IXBjkA==',
    )
    NCMR_BEARER_TOKEN = os.environ.get('HOLD_NCMR_TOKEN', '')

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

    # 缺陷代码全量同步（RPTDB → DEFECT_CODE）。只在正式环境的子进程里跑。
    # RPTDB 低于 12.1，子进程用 thick 模式；Web 进程不初始化 Instant Client。
    RPTDB_DSN = '10.181.160.15:1521/RPTDB'
    RPTDB_USER = 'ZNZZ_USER'
    RPTDB_PASSWORD = 'Znz3GvpcKU!PGV'
    ORACLE_CLIENT_LIB_DIR = os.environ.get(
        'ORACLE_CLIENT_LIB_DIR',
        r'E:\workspace\AutoHoldSys\oracle_dll',
    )
    DEFECT_SYNC_AT = os.environ.get('DEFECT_SYNC_AT', '02:00')
    DEFECT_SYNC_FT_CLASSIFY = '1_PD_DCG_DC_2_DN'
    DEFECT_SYNC_WLT_CLASSIFY = '1_PD_DCG_DC_2_DN_WLT'
    # 源表错误：WLT 分类下的该型号不参与同步，只保留 FT 分类的配置。
    DEFECT_SYNC_IGNORE = (
        ('1_PD_DCG_DC_2_DN_WLT', 'GC3003-3.5'),
    )

    # Hold info 合并为 hold_record 的定时任务配置。
    # 测试库和正式库表名相同，环境只切换 DB_PROFILES 里的连接。
    HOLD_INFO_TABLE = 'FT_HOLD_INFO'
    HOLD_RECORD_TABLE = 'FT_HOLD_RECORD'
    CIRCULATION_HISTORY_TABLE = 'CIRCULATION_HISTORY'
    # CP 合批单独成表，不写 FT_HOLD_RECORD / CIRCULATION_HISTORY。
    CP_HOLD_RECORD_TABLE, CP_CIRCULATION_TABLE = cp_hold_tables()
    HOLD_PREDICT_TABLE = 'FT_HOLD_PREDICT'
    # 源表关联 hold_record 的字段（TEST=HOLD_RECORD_ID；正式表迁移后同步修改）
    HOLD_INFO_LINK_COLUMN = 'HOLD_RECORD_ID'
    HOLD_MERGE_INTERVAL_MINUTES = 30
    # MES 已解 hold 但 record 未关闭时，合并任务内自动关闭（已关闭，不再按 HOLDING 结单）
    HOLD_AUTO_CLOSE_ENABLED = False
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
    # 梓一 / WLT 同 lot：组内 max(info ID) 不再变大，且距上次变大已满该分钟数，才建单。
    HOLD_LOT_QUIET_MINUTES = 30
    # 自首次看到该组起超过该分钟数仍在进新行，用已有行建单，避免一直不进待办。
    HOLD_LOT_QUIET_MAX_MINUTES = 120

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
        # AQL_HOLD 不再合批；手提创建见 annex_util.FT_MANUAL_HOLD_CODES
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