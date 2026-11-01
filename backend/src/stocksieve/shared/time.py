"""本文件提供统一的北京时间时区，供领域外的调度与资料处理复用。"""

from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo('Asia/Shanghai')
