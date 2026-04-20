"""
处理日期时间的工具
"""


month_map = {
    'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
    'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
    'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'
}

def format_ftp_date(raw_date: str) -> str:
    """
    将FTP LIST输出的日期格式 'Jan  6 03:23' 转换为 '01/06 03:23'
    """
    try:
        # 分割日期字符串
        parts = raw_date.split()
        if len(parts) < 3:
            return raw_date  # 无法解析，返回原格式
        
        month_str, day_str, time_str = parts[0], parts[1], parts[2]
        
        month_num = month_map.get(month_str)
        if not month_num:
            return raw_date
        
        # 格式化日期部分
        day_num = day_str.zfill(2)  # 确保两位数字
        date_part = f"{month_num}/{day_num}"
        
        # 格式化时间部分
        if ':' in time_str:
            time_parts = time_str.split(':')
            if len(time_parts) == 2:
                # 添加秒数
                time_formatted = f"{time_str}"
            else:
                time_formatted = time_str
        else:
            time_formatted = time_str
        
        return f"{date_part} {time_formatted}"
        
    except Exception as e:
        return raw_date