import logging
from dataclasses import dataclass
from typing import Optional

from .indicator_service import TrendDirection


@dataclass(slots=True)
class DerivativeRiskLimits:
    max_leverage: float = 5.0
    max_position_ratio: float = 0.3  # 单品种名义敞口 / 权益
    portfolio_exposure_limit: float = 3.0  # 组合总体杠杆上限
    risk_per_trade: float = 0.01  # 单笔风险占权益比例
    min_trend_strength: float = 1.0
    min_volatility_ratio: float = 0.001
    max_volatility_ratio: float = 0.03


@dataclass(slots=True)
class PositionState:
    size: float = 0.0
    side: Optional[TrendDirection] = None
    entry_price: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    unrealized_pnl: float = 0.0


class DerivativeRiskManager:
    """
    管理合约策略的杠杆、仓位与资金敞口。
    """

    def __init__(
        self,
        limits: DerivativeRiskLimits,
        contract_multiplier: float = 1.0,
    ) -> None:
        self.limits = limits
        self.contract_multiplier = contract_multiplier
        self.logger = logging.getLogger(self.__class__.__name__)

    def compute_position_size(
        self,
        equity: float,
        atr: float,
        price: float,
    ) -> float:
        if atr <= 0 or price <= 0 or equity <= 0:
            return 0.0

        risk_budget = equity * self.limits.risk_per_trade
        if risk_budget <= 0:
            return 0.0

        position_size = risk_budget / atr
        nominal_value = position_size * price * self.contract_multiplier
        nominal_cap = equity * self.limits.max_leverage
        if nominal_value > nominal_cap:
            position_size = nominal_cap / (price * self.contract_multiplier)

        return max(position_size, 0.0)

    def can_open(
        self,
        direction: TrendDirection,
        equity: float,
        snapshot,
        current_position: PositionState,
    ) -> bool:
        if direction is TrendDirection.FLAT:
            return False

        if snapshot.trend_strength < self.limits.min_trend_strength:
            self.logger.debug("趋势强度不足 %.2f < %.2f", snapshot.trend_strength, self.limits.min_trend_strength)
            return False

        if not (self.limits.min_volatility_ratio <= snapshot.volatility_ratio <= self.limits.max_volatility_ratio):
            self.logger.debug(
                "波动率不在区间 %.4f not in [%.4f, %.4f]",
                snapshot.volatility_ratio,
                self.limits.min_volatility_ratio,
                self.limits.max_volatility_ratio,
            )
            return False

        if current_position.size > 0 and current_position.side == direction:
            self.logger.debug("已有同方向仓位，跳过加仓")
            return False

        existing_nominal = current_position.size * snapshot.close * self.contract_multiplier
        if existing_nominal >= equity * self.limits.max_position_ratio:
            self.logger.info("仓位敞口 %.2f 超出 %.2f，禁止开仓", existing_nominal, equity * self.limits.max_position_ratio)
            return False

        return equity > 0

    def should_reduce_on_drawdown(
        self,
        current_price: float,
        position: PositionState,
        snapshot,
    ) -> bool:
        if position.size <= 0:
            return False
        if position.side is TrendDirection.LONG and current_price < snapshot.keltner_lower:
            return True
        if position.side is TrendDirection.SHORT and current_price > snapshot.keltner_upper:
            return True
        return False
