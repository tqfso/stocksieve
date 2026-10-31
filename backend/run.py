"""本文件作为 uv 项目的运行入口，加载 src 中的后端应用。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

from stocksieve.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
