import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    side: str
    amount: float
    order_type: str = "market"
    price: Optional[float] = None
    reduce_only: bool = False
    post_only: bool = False
    client_order_id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)


class OrderExecutor:
    """
    统一封装合约下单流程，处理 reduce_only/post_only、重试、日志记录等细节。
    """

    def __init__(
        self,
        exchange_client,
        *,
        max_retries: int = 3,
        default_time_in_force: str = "GTC",
    ) -> None:
        self.exchange = exchange_client
        self.max_retries = max_retries
        self.default_time_in_force = default_time_in_force
        self.logger = logging.getLogger(self.__class__.__name__)

    async def submit(self, order: OrderRequest) -> Dict[str, Any]:
        params = order.params.copy()
        params.setdefault("timeInForce", self.default_time_in_force)
        if order.reduce_only:
            params["reduceOnly"] = True
        if order.post_only:
            params["postOnly"] = True
        if order.client_order_id:
            params["newClientOrderId"] = order.client_order_id

        attempt = 0
        last_error: Optional[Exception] = None
        while attempt < self.max_retries:
            try:
                self.logger.info(
                    "[%s] 下单: type=%s side=%s amount=%.6f price=%s reduce_only=%s post_only=%s",
                    order.symbol,
                    order.order_type,
                    order.side,
                    order.amount,
                    order.price,
                    order.reduce_only,
                    order.post_only,
                )
                response = await self.exchange.create_order(
                    symbol=order.symbol,
                    order_type=order.order_type,
                    side=order.side,
                    amount=order.amount,
                    price=order.price,
                    params=params,
                )
                return response or {}
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                last_error = exc
                self.logger.warning(
                    "下单失败 (%s) attempt=%s/%s error=%s",
                    order.symbol,
                    attempt,
                    self.max_retries,
                    exc,
                )
        if last_error:
            raise last_error
        return {}

    async def cancel(self, symbol: str, order_id: str) -> Dict[str, Any]:
        self.logger.info("取消订单 %s@%s", order_id, symbol)
        return await self.exchange.cancel_order(order_id, symbol)
