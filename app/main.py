import sys
import os
import argparse
import signal
import threading
from multiprocessing import freeze_support
from cryptography import x509
from cryptography.hazmat.primitives.kdf import pbkdf2
from waitress import serve
# ==========================================
# 路径配置
# ==========================================
if getattr(sys, 'frozen', False):
    # 冻结后日志、临时目录写到 exe 所在目录，而不是解包临时目录
    os.chdir(os.path.dirname(os.path.abspath(sys.executable)))
    project_root_path = sys._MEIPASS
else:
    current_file_path = os.path.abspath(__file__)
    app_folder_path = os.path.dirname(current_file_path)
    project_root_path = os.path.dirname(app_folder_path)
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)

# ==========================================
# 应用初始化
# ==========================================
from app import create_app, db
from app.config import Config
# 直接从 routes 包中导入所有蓝图
from app.routes import user_bp, auth_bp, product_bp, defect_bp, test_data_bp, common_data_bp, rawdata_bp, hold_report_bp, dispose_bp, engineer_bp, production_bp, quality_bp, client_error_bp
from app.backend_schedule.FT_WLT_TESTLOG_sche import FlaskTaskScheduler
from app.backend_schedule.FT_HOLD_MERGE_sche import HoldMergeScheduler

app = create_app()

# ==========================================
# 注册蓝图
# ==========================================
app.register_blueprint(user_bp)   # 注册用户管理模块 (/admin/users)
app.register_blueprint(auth_bp)   # 注册认证模块 (/login, /api/login)
app.register_blueprint(product_bp)
app.register_blueprint(defect_bp)
app.register_blueprint(test_data_bp)
app.register_blueprint(common_data_bp)
app.register_blueprint(rawdata_bp)
app.register_blueprint(hold_report_bp)  # Hold 报表 (/admin/hold/...)
app.register_blueprint(dispose_bp)      # Hold 处置流转 (/admin/hold/api/dispose...)
app.register_blueprint(engineer_bp)     # 产品工程师 (/eng/...)
app.register_blueprint(production_bp)   # 生产 (/prod/...)
app.register_blueprint(quality_bp)      # 质量部只读报表 (/qa/...)
app.register_blueprint(client_error_bp)  # 客户端崩溃上报 (/api/client_errors)

# ==========================================
# 程序入口
# ==========================================
def _install_interrupt_handlers(stop_event):
    """让 Ctrl+C / Ctrl+Break / SIGTERM 能打断 Windows 上阻塞的 serve 循环。"""
    def _handle(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _handle)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, _handle)


def _wait_until_stop(stop_event, server_thread):
    try:
        while not stop_event.is_set() and server_thread.is_alive():
            if stop_event.wait(0.5):
                break
    except KeyboardInterrupt:
        stop_event.set()


def _shutdown(schedulers):
    print('\n正在退出...')
    for sched in schedulers:
        try:
            sched.stop()
        except Exception:
            pass
    # Waitress / FTP 等阻塞调用在 Windows 上经常无法干净 join
    os._exit(0)


if __name__ == '__main__':
    freeze_support()
    parser = argparse.ArgumentParser(description='启动 Flask 应用，可选 debug/release 模式。默认 release。')
    parser.add_argument(
        '--mode',
        choices=['debug', 'release'],
        default='release',
        help='运行模式：debug 启动 Hold 合并调度；release 另启动 testlog / 预测等后台任务。',
    )
    args = parser.parse_args()
    is_debug_mode = args.mode == 'debug'

    with app.app_context():
        db.create_all()
    
    print(f"运行模式: {args.mode}")
    print("\n=== 🚀 已注册的路由 ===")
    for rule in app.url_map.iter_rules():
        methods = ','.join(sorted([m for m in rule.methods if m not in ('HEAD', 'OPTIONS')]))
        print(f"{methods:7} {rule.rule} -> {rule.endpoint}")
    print("======================\n")
    print("按 Ctrl+C 结束运行\n")

    stop_event = threading.Event()
    _install_interrupt_handlers(stop_event)
    schedulers = []

    hold_merge_scheduler = HoldMergeScheduler()
    hold_merge_scheduler.start()
    schedulers.append(hold_merge_scheduler)

    if not is_debug_mode:
        task_scheduler = FlaskTaskScheduler()
        task_scheduler.start()
        schedulers.append(task_scheduler)
        if getattr(Config, 'HOLD_PREDICT_ENABLED', False):
            from app.backend_schedule.FT_HOLD_PREDICT_sche import HoldPredictScheduler
            hold_predict_scheduler = HoldPredictScheduler()
            hold_predict_scheduler.start()
            schedulers.append(hold_predict_scheduler)
        else:
            print('HOLD_PREDICT_ENABLED=False，未启动可放行概率预测调度')
        server = threading.Thread(
            target=lambda: serve(app, host='0.0.0.0', port=50001),
            name='waitress',
            daemon=True,
        )
    else:
        # 关闭 reloader，避免父子双进程导致 Ctrl+C 杀不干净
        server = threading.Thread(
            target=lambda: app.run(
                host='0.0.0.0', debug=True, port=50001, use_reloader=False,
            ),
            name='flask-debug',
            daemon=True,
        )

    server.start()
    _wait_until_stop(stop_event, server)
    _shutdown(schedulers)
 