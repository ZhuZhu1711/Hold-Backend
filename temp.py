import oracledb
import re
from datetime import datetime

# ================= 配置区域 =================
# 数据库连接信息
USER = "FT_OWEN"
PWD = "Mee0MvpgXU!Lcp"
DSN = "172.18.202.5:1521/jsqy"

# ================= 配置区域 =================
DB_CONFIG = {
    "user": "FT_OWEN",
    "password": PWD,
    "dsn": DSN 
}

# 匹配 /MAP_CP_PDF/ 后面紧跟着 TryNewXML 的情况
# 这意味着中间缺了 Data[...] 目录
PATTERN_MISSING = re.compile(r"(/MAP_CP_PDF/)(TryNewXML.*)")
# =============================================

def fix_missing_data_dir():
    print("正在连接数据库...")
    try:
        connection = oracledb.connect(**DB_CONFIG)
        print("数据库连接成功！")
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return

    cursor = connection.cursor()
    
    try:
        # 1. 查询逻辑：
        # 1. 路径包含 MAP_CP_PDF 和 TryNewXML
        # 2. 路径中 NOT LIKE '%Data[%' (筛选出缺失 Data[...] 的记录)
        query_sql = """
            SELECT "ID", "FTP_PATH", "TEST_DATE"
            FROM "FT_OWEN"."FT_WLT_TESTLOG" 
            WHERE "FTP_PATH" LIKE '%/MAP_CP_PDF/TryNewXML%'
            AND "FTP_PATH" NOT LIKE '%Data[%'
        """
        
        cursor.execute(query_sql)
        rows = cursor.fetchall()
        
        if not rows:
            print("✅ 未发现缺失 Data[...] 目录的记录。")
            return

        print(f"🔍 发现 {len(rows)} 条缺失 Data[...] 的记录，开始修复...")
        
        update_sql = """
            UPDATE "FT_OWEN"."FT_WLT_TESTLOG" 
            SET "FTP_PATH" = :new_path 
            WHERE "ID" = :id
        """
        
        batch_data = []
        
        for row_id, original_path, test_date in rows:
            if not test_date:
                print(f"⚠️ ID {row_id} 缺失 TEST_DATE，跳过。")
                continue

            # 1. 格式化日期
            # 你的示例是 Data[2026-4-15]，即 YYYY-M-D (去除前导零)
            # Python 的 %-m 和 %-d 在 Windows 下可能不兼容，这里用 lstrip('0') 兼容所有系统
            year = test_date.strftime('%Y')
            month = str(int(test_date.strftime('%m'))) # 去除前导零
            day = str(int(test_date.strftime('%d')))   # 去除前导零
            
            data_dir = f"Data[{year}-{month}-{day}]"
            
            # 2. 插入目录
            # 将 /MAP_CP_PDF/TryNewXML... 替换为 /MAP_CP_PDF/Data[...]/TryNewXML...
            fixed_path = PATTERN_MISSING.sub(rf"\1{data_dir}/\2", original_path)
            
            batch_data.append((fixed_path, row_id))
            
            # 打印前 5 条预览
            if len(batch_data) <= 5:
                print(f"   [原] {original_path}")
                print(f"   [新] {fixed_path}")
                print("-" * 60)

        # 3. 执行批量更新
        if batch_data:
            cursor.executemany(update_sql, batch_data)
            connection.commit()
            print(f"🚀 成功更新 {len(batch_data)} 条记录！")
        else:
            print("没有数据需要更新。")

    except Exception as e:
        connection.rollback()
        print(f"❌ 处理过程中发生错误，已回滚: {e}")
    finally:
        cursor.close()
        connection.close()
        print("数据库连接已关闭。")

if __name__ == "__main__":
    fix_missing_data_dir()