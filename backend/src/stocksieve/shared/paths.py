"""本文件集中定位后端资源根目录，分包不改变配置、SQL、技能及静态文件的位置。"""

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
