import ftplib
from queue import Queue, Empty
import time

class RobustFtpPool:
    def __init__(self, host, user, passwd, max_connections=20, timeout=10):
        self.host = host
        self.user = user
        self.passwd = passwd
        self.timeout = timeout
        self.pool = Queue(maxsize=max_connections)
        
        # 初始化连接
        for _ in range(max_connections):
            self._add_to_pool()

    def _create_connection(self):
        """真正建立物理连接"""
        try:
            ftp = ftplib.FTP()
            ftp.connect(self.host, timeout=self.timeout)
            ftp.login(self.user, self.passwd)
            ftp.voidcmd('TYPE I') # 二进制模式
            return ftp
        except Exception as e:
            print(f"❌ 创建连接失败: {e}")
            return None

    def _add_to_pool(self):
        conn = self._create_connection()
        if conn:
            self.pool.put(conn)

    def get_conn(self):
        """
            借出连接 + 坏一补一
        """
        while True:
            try:
                conn = self.pool.get(block=True, timeout=5)
                
                # --- 核心：借出前验活 ---
                try:
                    conn.voidcmd('NOOP')
                    return conn # 1. 连接健康，直接返回
                except Exception:
                    # 2. 连接已死，丢弃它，并且【立刻补充一个新连接】
                    self._add_to_pool() 
                    continue 
                    
            except Empty:
                # 3. 池子空了（极端情况），临时创建一个
                temp_conn = self._create_connection()
                if temp_conn: return temp_conn
                time.sleep(0.1)

    def return_conn(self, conn):
        """归还连接"""
        # 简单策略：直接扔回池子
        try:
            if conn.sock: # 简单检查 socket 是否存在
                 self.pool.put(conn)
            else:
                 self._add_to_pool() # 补一个
        except:
            self._add_to_pool()

testlog_ftp_pool = RobustFtpPool(
    "172.18.200.250",
    "share",
    "abc@123"
)
