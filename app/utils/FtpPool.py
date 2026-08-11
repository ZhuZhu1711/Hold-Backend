import ftplib
import logging
from queue import Queue, Empty
import time

logger = logging.getLogger(__name__)


class FtpUnavailableError(ConnectionError):
    """FTP 不可用：仅影响依赖 testlog 下载的数据分析，不影响 Hold 流转。"""


class RobustFtpPool:
    """
    懒加载 FTP 连接池：模块导入 / 进程启动时不建连。
    仅在 get_conn() 时尝试连接；失败快速抛错，由调用方处理。
    """

    def __init__(self, host, user, passwd, max_connections=20, timeout=10):
        self.host = host
        self.user = user
        self.passwd = passwd
        self.timeout = timeout
        self.max_connections = max_connections
        self.pool = Queue(maxsize=max_connections)

    def _create_connection(self):
        """真正建立物理连接；失败返回 None，不抛到启动路径。"""
        try:
            ftp = ftplib.FTP()
            ftp.connect(self.host, timeout=self.timeout)
            ftp.login(self.user, self.passwd)
            ftp.voidcmd('TYPE I')  # 二进制模式
            return ftp
        except Exception as e:
            logger.warning('FTP connect failed (%s): %s', self.host, e)
            return None

    def get_conn(self, retries=3):
        """
        借出连接。池空或连接失效时按需新建。
        连续失败则抛 FtpUnavailableError，避免死循环拖死请求。
        """
        last_err = None
        for _ in range(max(1, int(retries))):
            try:
                conn = self.pool.get(block=False)
                try:
                    conn.voidcmd('NOOP')
                    return conn
                except Exception as e:
                    last_err = e
                    try:
                        conn.close()
                    except Exception:
                        pass
            except Empty:
                pass

            conn = self._create_connection()
            if conn:
                return conn
            time.sleep(0.15)

        raise FtpUnavailableError(
            f'FTP unavailable: {self.host}'
            + (f' ({last_err})' if last_err else '')
        )

    def return_conn(self, conn):
        """归还连接；池满或无效则关闭。"""
        if conn is None:
            return
        try:
            if getattr(conn, 'sock', None) is None:
                try:
                    conn.close()
                except Exception:
                    pass
                return
            try:
                self.pool.put_nowait(conn)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            try:
                conn.close()
            except Exception:
                pass


# 仅构造空池，启动时不连接 FTP
testlog_ftp_pool = RobustFtpPool(
    '172.18.200.250',
    'share',
    'abc@123',
)
