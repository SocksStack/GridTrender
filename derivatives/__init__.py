"""
衍生品策略相关模块。
"""

from .exchange_client import DerivativeExchangeClient
from .indicator_service import IndicatorService, IndicatorSnapshot, TrendDirection
from .order_executor import OrderExecutor, OrderRequest
from .risk_manager import DerivativeRiskLimits, DerivativeRiskManager, PositionState
from .trend_trader import DerivativeStrategyConfig, DerivativeTrendTrader

__all__ = [
    "DerivativeExchangeClient",
    "IndicatorService",
    "IndicatorSnapshot",
    "TrendDirection",
    "OrderExecutor",
    "OrderRequest",
    "DerivativeRiskLimits",
    "DerivativeRiskManager",
    "PositionState",
    "DerivativeStrategyConfig",
    "DerivativeTrendTrader",
]
