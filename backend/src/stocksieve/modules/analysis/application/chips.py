"""本文件提供独立筹码查询，与AI任务共用计算能力，不创建报告或调用模型。"""

from typing import Any

from stocksieve.modules.analysis.application.facts import shareholder_facts
from stocksieve.modules.analysis.domain.calculations import chip_distribution
from stocksieve.modules.market.application.queries import Market
from stocksieve.shared.errors import MissingData


class Chips:
    """筹码缺失以正常查询结果说明，不阻断个股其他功能。"""

    def __init__(self, market: Market) -> None:
        self.market = market

    def get(self, stock_id: str, snapshot_id: str | None) -> dict[str, Any]:
        stock = self.market.stock(stock_id, snapshot_id)
        with self.market.db.transaction() as connection:
            snapshot = self.market.snapshot(connection, stock['snapshot']['id'])
            history = self.market.history(connection, snapshot, stock_id)
        result: dict[str, Any] = {'stockId': stock_id, 'snapshotId': snapshot['id'], 'dataDate': snapshot['data_date'],
            'method': '换手衰减成本估算 v1；取最近最多120个交易日，首日及每日成交成本按当日高低价间均匀分布，随换手衰减。',
            'distribution': None, 'missingInformation': [],
            'shareholders': shareholder_facts()}
        try:
            result['distribution'] = chip_distribution(history, snapshot['data_date'])
        except MissingData as error:
            result['missingInformation'] = [str(error)]
        return result
