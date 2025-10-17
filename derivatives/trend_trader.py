import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from .abstract_trader import AbstractTrader
from .indicator_service import IndicatorService, IndicatorSnapshot, TrendDirection
from .order_executor import OrderExecutor, OrderRequest
from .risk_manager import DerivativeRiskLimits, DerivativeRiskManager, PositionState

Number = float


@dataclass(slots=True)
class DerivativeStrategyConfig:
    symbol: str
    signal_timeframe: str = "1h"
    execution_timeframe: str = "15m"
    signal_lookback: int = 240
    execution_lookback: int = 180
    ema_fast: int = 50
    ema_slow: int = 200
    adx_period: int = 14
    adx_threshold: float = 25.0
    atr_period: int = 21
    donchian_period: int = 20
    keltner_multiplier: float = 2.0
    atr_stop_multiplier: float = 2.5
    trailing_atr_multiplier: float = 3.0
    loop_interval: float = 60.0
    contract_multiplier: float = 1.0
    risk_limits: DerivativeRiskLimits = field(
        default_factory=lambda: DerivativeRiskLimits()
    )


class DerivativeTrendTrader(AbstractTrader):
    """
    趋势 + ATR 合约交易器，按周期执行信号评估、仓位管理与风控。
    """

    def __init__(
        self,
        exchange_client,
        config: DerivativeStrategyConfig,
        indicator_service: Optional[IndicatorService] = None,
        order_executor: Optional[OrderExecutor] = None,
        risk_manager: Optional[DerivativeRiskManager] = None,
    ) -> None:
        super().__init__(loop_interval=config.loop_interval)
        self.exchange = exchange_client
        self.config = config
        self.indicators = indicator_service or IndicatorService()
        self.order_executor = order_executor or OrderExecutor(exchange_client)
        risk_limits = config.risk_limits
        self.risk_manager = risk_manager or DerivativeRiskManager(
            risk_limits, contract_multiplier=config.contract_multiplier
        )
        self.risk_manager.limits.min_trend_strength = max(
            self.risk_manager.limits.min_trend_strength,
            getattr(config.risk_limits, "min_trend_strength", 0.0),
        )
        self.position_state = PositionState()
        self.market_info = None
        self.last_snapshot: Optional[IndicatorSnapshot] = None
        self.start_time: Optional[float] = None
        self.logger = logging.getLogger(self.__class__.__name__)

    async def initialize(self) -> None:
        self.logger.info("初始化合约策略: %s", self.config.symbol)
        await self.exchange.load_markets()
        await self.exchange.ensure_contract_setup(self.config.symbol)
        self.start_time = time.time()
        self.market_info = self.exchange.exchange.market(self.config.symbol)
        await self._sync_position_state()

    async def step(self) -> None:
        signal_candles = await self._fetch_ohlcv(
            self.config.signal_timeframe, self.config.signal_lookback
        )
        execution_candles = await self._fetch_ohlcv(
            self.config.execution_timeframe, self.config.execution_lookback
        )
        snapshot = self.indicators.build_snapshot(
            signal_candles,
            execution_candles,
            ema_fast=self.config.ema_fast,
            ema_slow=self.config.ema_slow,
            adx_period=self.config.adx_period,
            atr_period=self.config.atr_period,
            donchian_period=self.config.donchian_period,
            keltner_multiplier=self.config.keltner_multiplier,
        )
        if snapshot is None:
            return

        self.last_snapshot = snapshot
        await self._sync_position_state()
        account_metrics = await self.exchange.fetch_account_metrics()
        equity = max(float(account_metrics.get("equity", 0.0)), 0.0)

        direction = self._determine_trend(snapshot)
        if direction is TrendDirection.FLAT:
            await self._maybe_flatten_on_trend_flip(snapshot)
            return

        if self.position_state.side and self.position_state.side != direction:
            self.logger.info("趋势反转，平掉旧仓位 %s", self.position_state.side)
            await self._close_position("trend_flip")

        if self.position_state.side:
            await self._maybe_trail_stop(snapshot)
            if self.risk_manager.should_reduce_on_drawdown(
                snapshot.close, self.position_state, snapshot
            ):
                await self._close_position("keltner_break")
            return

        if not self._should_open_trade(direction, snapshot):
            return

        if not self.risk_manager.can_open(
            direction, equity, snapshot, self.position_state
        ):
            return

        await self._open_position(direction, snapshot, equity)

    async def shutdown(self) -> None:
        self.logger.info("停止衍生品策略: %s", self.config.symbol)
        try:
            await self.exchange.close()
        except Exception:  # noqa: BLE001
            self.logger.debug("关闭交易客户端遇到异常", exc_info=True)

    async def _fetch_ohlcv(self, timeframe: str, limit: int):
        return await self.exchange.fetch_ohlcv(
            self.config.symbol, timeframe=timeframe, limit=limit
        )

    def _determine_trend(self, snapshot: IndicatorSnapshot) -> TrendDirection:
        if snapshot.adx < self.config.adx_threshold:
            return TrendDirection.FLAT
        if snapshot.ema_fast > snapshot.ema_slow:
            return TrendDirection.LONG
        if snapshot.ema_fast < snapshot.ema_slow:
            return TrendDirection.SHORT
        return TrendDirection.FLAT

    def _should_open_trade(
        self, direction: TrendDirection, snapshot: IndicatorSnapshot
    ) -> bool:
        if direction is TrendDirection.LONG:
            return snapshot.close >= snapshot.donchian_high
        if direction is TrendDirection.SHORT:
            return snapshot.close <= snapshot.donchian_low
        return False

    async def _open_position(
        self,
        direction: TrendDirection,
        snapshot: IndicatorSnapshot,
        equity: Number,
    ) -> None:
        position_size = self.risk_manager.compute_position_size(
            equity=equity,
            atr=snapshot.atr,
            price=snapshot.close,
        )
        amount = self._apply_precision(position_size)
        if amount <= 0:
            self.logger.warning("计算得到的仓位数量 <= 0，放弃开仓")
            return

        side = "buy" if direction is TrendDirection.LONG else "sell"
        response = await self.order_executor.submit(
            OrderRequest(
                symbol=self.config.symbol,
                side=side,
                amount=amount,
                order_type="market",
                reduce_only=False,
            )
        )
        self.logger.info("开仓成功: %s", response)
        entry_price = float(response.get("average") or snapshot.close)
        self.position_state = PositionState(
            size=amount,
            side=direction,
            entry_price=entry_price,
            stop_loss=self._calculate_stop(direction, entry_price, snapshot.atr),
        )

    async def _close_position(self, reason: str) -> None:
        if not self.position_state.side or self.position_state.size <= 0:
            return
        side = (
            "sell" if self.position_state.side is TrendDirection.LONG else "buy"
        )
        amount = self._apply_precision(self.position_state.size)
        if amount <= 0:
            return
        response = await self.order_executor.submit(
            OrderRequest(
                symbol=self.config.symbol,
                side=side,
                amount=amount,
                order_type="market",
                reduce_only=True,
            )
        )
        self.logger.info("平仓成功 reason=%s resp=%s", reason, response)
        self.position_state = PositionState()

    async def _maybe_flatten_on_trend_flip(self, snapshot: IndicatorSnapshot) -> None:
        if not self.position_state.side:
            return
        if self.position_state.side is TrendDirection.LONG and snapshot.close < snapshot.ema_slow:
            await self._close_position("trend_exit")
        elif self.position_state.side is TrendDirection.SHORT and snapshot.close > snapshot.ema_slow:
            await self._close_position("trend_exit")

    async def _maybe_trail_stop(self, snapshot: IndicatorSnapshot) -> None:
        if not self.position_state.side or self.position_state.size <= 0:
            return
        trail_offset = self.config.trailing_atr_multiplier * snapshot.atr
        if self.position_state.side is TrendDirection.LONG:
            new_stop = max(
                self.position_state.stop_loss or 0.0,
                snapshot.close - trail_offset,
            )
        else:
            new_stop = min(
                self.position_state.stop_loss or float("inf"),
                snapshot.close + trail_offset,
            )
        if self.position_state.stop_loss != new_stop:
            self.logger.info(
                "更新移动止损 %.4f -> %.4f", self.position_state.stop_loss, new_stop
            )
            self.position_state.stop_loss = new_stop

    def _calculate_stop(
        self, direction: TrendDirection, entry_price: Number, atr: Number
    ) -> Number:
        offset = self.config.atr_stop_multiplier * atr
        if direction is TrendDirection.LONG:
            return max(entry_price - offset, 0.0)
        return entry_price + offset

    async def _sync_position_state(self) -> None:
        position = await self.exchange.fetch_position(self.config.symbol)
        if not position:
            self.position_state = PositionState()
            return
        contracts = abs(float(position.get("contracts") or position.get("amount") or 0.0))
        if contracts <= 0:
            self.position_state = PositionState()
            return
        entry_price = float(position.get("entryPrice") or position.get("markPrice") or 0.0)
        side_str = position.get("side") or ("long" if float(position.get("contracts", 0)) > 0 else "short")
        side = (
            TrendDirection.LONG
            if str(side_str).lower().startswith("long")
            else TrendDirection.SHORT
        )
        unrealized = float(
            position.get("unrealizedPnl")
            or position.get("unrealizedProfit")
            or 0.0
        )
        self.position_state = PositionState(
            size=contracts,
            side=side,
            entry_price=entry_price,
            stop_loss=self.position_state.stop_loss,
            take_profit=self.position_state.take_profit,
            unrealized_pnl=unrealized,
        )

    def _apply_precision(self, amount: Number) -> Number:
        if not self.market_info:
            return amount
        precision = self.market_info.get("precision", {}).get("amount")
        if precision is None:
            return amount
        factor = 10 ** precision
        return math.floor(amount * factor) / factor
