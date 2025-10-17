import logging
import os
import time
from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt

from config import settings
from exchange_client import ExchangeClient


class DerivativeExchangeClient(ExchangeClient):
    """
    Binance U 本位永续合约客户端，复用基础 ExchangeClient 的工具方法，
    同时提供杠杆、仓位、资金费率等衍生品专属接口。
    """

    def __init__(
        self,
        leverage: Optional[float] = None,
        margin_mode: str = "cross",
        settle: str = "USDT",
    ) -> None:
        # 初始化基础属性
        super().__init__()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.market_type = "future"
        self.default_settle = settle
        self.default_leverage = leverage
        self.default_margin_mode = margin_mode.lower() if margin_mode else None

        proxy = os.getenv("HTTP_PROXY")
        self.exchange = ccxt.binance(
            {
                "apiKey": settings.BINANCE_API_KEY,
                "secret": settings.BINANCE_API_SECRET,
                "enableRateLimit": True,
                "timeout": 60000,
                "options": {
                    "defaultType": "future",
                    "defaultSubType": "linear",
                    "defaultSettle": settle,
                    "recvWindow": 5000,
                    "adjustForTimeDifference": True,
                    "warnOnFetchOpenOrdersWithoutSymbol": False,
                    "createMarketBuyOrderRequiresPrice": False,
                },
                "aiohttp_proxy": proxy,
                "verbose": settings.DEBUG_MODE,
            }
        )

        if proxy:
            self.logger.info("使用代理访问合约接口: %s", proxy)

        # 合约账户不需要现货储蓄缓存，重置相关缓存结构
        self.balance_cache = {"timestamp": 0, "data": None}
        self.funding_balance_cache = {"timestamp": 0, "data": {}}

        self.logger.info(
            "DerivativeExchangeClient 初始化完成 (settle=%s, leverage=%s, margin_mode=%s)",
            settle,
            leverage,
            self.default_margin_mode,
        )

    async def fetch_balance(self, params: Optional[Dict[str, Any]] = None):
        """
        默认请求合约账户权益，可通过 params 覆盖。
        """
        params = params.copy() if params else {}
        params.setdefault("type", "future")
        return await super().fetch_balance(params)

    async def fetch_positions(
        self, symbols: Optional[List[str]] = None, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        if not self.markets_loaded:
            await self.load_markets()
        positions = await self.exchange.fetch_positions(symbols, params or {})
        return positions or []

    async def fetch_position(
        self, symbol: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        positions = await self.fetch_positions([symbol], params)
        for position in positions:
            if position.get("symbol") == symbol:
                return position
        return None

    async def set_leverage(self, symbol: str, leverage: float) -> None:
        """
        设置默认杠杆，捕获常见异常避免终止主流程。
        """
        try:
            await self.exchange.set_leverage(leverage, symbol)
            self.logger.info("设置杠杆 leverage=%s for %s", leverage, symbol)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("设置杠杆失败(%s): %s", symbol, exc)

    async def set_margin_mode(self, symbol: str, mode: str) -> None:
        """
        mode: 'cross' or 'isolated'
        """
        try:
            await self.exchange.set_margin_mode(mode, symbol)
            self.logger.info("设置保证金模式 %s for %s", mode, symbol)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("设置保证金模式失败(%s): %s", symbol, exc)

    async def ensure_contract_setup(self, symbol: str) -> None:
        if self.default_margin_mode:
            await self.set_margin_mode(symbol, self.default_margin_mode)
        if self.default_leverage:
            await self.set_leverage(symbol, self.default_leverage)

    async def fetch_funding_rate(
        self, symbol: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        try:
            return await self.exchange.fetch_funding_rate(symbol, params or {})
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("获取资金费率失败(%s): %s", symbol, exc)
            return None

    async def fetch_funding_rates(self, symbols: Optional[List[str]] = None):
        try:
            return await self.exchange.fetch_funding_rates(symbols or [])
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("获取资金费率列表失败: %s", exc)
            return []

    async def fetch_account_metrics(self) -> Dict[str, Any]:
        balance = await self.fetch_balance()
        info = balance.get("info", {}) if isinstance(balance, dict) else {}
        # Binance future balance 返回的 info 包含账户权益等字段
        metrics = {
            "equity": float(info.get("totalWalletBalance", 0.0)),
            "unrealized_profit": float(info.get("totalUnrealizedProfit", 0.0)),
            "margin_balance": float(info.get("totalMarginBalance", 0.0)),
        }
        return metrics

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ):
        params = params.copy() if params else {}
        params.setdefault("timestamp", int(time.time() * 1000 + self.time_diff))
        params.setdefault("recvWindow", 5000)
        await self.sync_time()
        return await self.exchange.create_order(symbol, order_type, side, amount, price, params)
