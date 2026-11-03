"""本文件读取后端环境配置，凭据不进入默认日志或公开接口。"""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import URL

from stocksieve.shared.market_types import BOARDS
from stocksieve.shared.paths import BACKEND_ROOT

ROOT = BACKEND_ROOT


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    state_dir: Path = ROOT / '.state'
    web_dir: Path = ROOT / 'web'
    rate_dir: Path = ROOT / '.source-probe'
    market_boards: tuple[str, ...] = tuple(BOARDS)
    enabled_sources: tuple[str, ...] = ()
    default_source: str = 'sina'
    request_interval: float = 0.2
    source_daily_limit: int = 20000
    history_trading_days: int = 150
    history_max_trading_days: int = 0
    retry_minutes: int = 10
    readiness_hours: int = 4
    lease_seconds: int = 120
    ai_base_url: str | None = None
    ai_api_key: str | None = field(default=None, repr=False)
    ai_model: str | None = None
    ai_protocol: str = 'chat_completions'
    ai_max_steps: int = 12
    ai_tools_supported: bool = True
    ai_mode: str = 'mock'
    testing: bool = False

    @classmethod
    def from_env(cls) -> 'Settings':
        values: dict[str, str] = {}
        central = ROOT / '.env'
        # 统一配置存在时只读取它；旧位置仅兼容尚未迁移的独立数据库部署。
        for local in (central,) if central.exists() else (ROOT / 'deploy/postgres/.env',):
            if not local.exists():
                continue
            for line in local.read_text().splitlines():
                if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    values[key.strip()] = value.strip().strip('"\'')
        values.update(os.environ)
        url = values.get('STOCKSIEVE_DATABASE_URL')
        if not url:
            password = values.get('STOCKSIEVE_POSTGRES_PASSWORD')
            if not password:
                raise RuntimeError('请配置 STOCKSIEVE_DATABASE_URL 或既有 PostgreSQL 的 .env。')
            url = URL.create('postgresql+psycopg', username=values.get('STOCKSIEVE_POSTGRES_USER', 'stocksieve_admin'),
                             password=password, host=values.get('STOCKSIEVE_POSTGRES_HOST', '127.0.0.1'), port=int(values.get('STOCKSIEVE_POSTGRES_PORT', '5432')),
                             database=values.get('STOCKSIEVE_POSTGRES_DB', 'stocksieve')).render_as_string(hide_password=False)
        return cls(database_url=url, state_dir=Path(values.get('STOCKSIEVE_STATE_DIR', str(ROOT / '.state'))),
                   web_dir=Path(values.get('STOCKSIEVE_WEB_DIR', str(ROOT / 'web'))),
                   rate_dir=Path(values.get('STOCKSIEVE_RATE_DIR', str(ROOT / '.source-probe'))),
                   market_boards=tuple(filter(None, values.get('STOCKSIEVE_MARKET_BOARDS', ','.join(BOARDS)).split(','))),
                   enabled_sources=tuple(filter(None, values.get('STOCKSIEVE_ENABLED_SOURCES', '').split(','))),
                   default_source=values.get('STOCKSIEVE_DEFAULT_SOURCE', 'sina'),
                   request_interval=float(values.get('STOCKSIEVE_SOURCE_INTERVAL_SECONDS', '0.2')),
                   source_daily_limit=int(values.get('STOCKSIEVE_SOURCE_DAILY_LIMIT', '20000')),
                   history_trading_days=int(values.get('STOCKSIEVE_HISTORY_TRADING_DAYS', '150')),
                   history_max_trading_days=int(values.get('STOCKSIEVE_HISTORY_MAX_TRADING_DAYS', '0')),
                   ai_base_url=values.get('STOCKSIEVE_AI_BASE_URL'), ai_api_key=values.get('STOCKSIEVE_AI_API_KEY'),
                   ai_model=values.get('STOCKSIEVE_AI_MODEL'), ai_protocol=values.get('STOCKSIEVE_AI_PROTOCOL', 'chat_completions'),
                   ai_mode=values.get('STOCKSIEVE_AI_MODE', 'mock'),
                   ai_tools_supported=cls.boolean(values.get('STOCKSIEVE_AI_TOOLS_SUPPORTED', 'true')))

    @staticmethod
    def boolean(value: str) -> bool:
        if value.lower() not in {'true', 'false'}:
            raise ValueError('布尔配置需为 true 或 false。')
        return value.lower() == 'true'

    def __post_init__(self) -> None:
        if type(self.history_trading_days) is not int or self.history_trading_days < 140:
            raise ValueError('STOCKSIEVE_HISTORY_TRADING_DAYS需为至少140的整数。')
        if type(self.history_max_trading_days) is not int or self.history_max_trading_days < 0 or self.history_max_trading_days and self.history_max_trading_days < self.history_trading_days:
            raise ValueError('STOCKSIEVE_HISTORY_MAX_TRADING_DAYS需为0，或不小于补齐天数。')
        if not math.isfinite(self.request_interval) or self.request_interval < 0.2 or self.source_daily_limit < 1:
            raise ValueError('渠道间隔不得低于0.2秒，日预算必须为正数。')
        if self.ai_protocol not in {'chat_completions', 'responses'}:
            raise ValueError('AI 协议配置无效。')
        if self.ai_mode not in {'mock', 'live'}:
            raise ValueError('AI 运行模式需为 mock 或 live。')
        if not self.market_boards or len(set(self.market_boards)) != len(self.market_boards) or set(self.market_boards) - BOARDS.keys():
            raise ValueError('上市板块配置为空、重复或包含未知板块。')
        if self.lease_seconds <= 0 or not 1 <= self.ai_max_steps <= 50:
            raise ValueError('租约和分析调用上限配置无效。')
