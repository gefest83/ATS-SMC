"""Paper, testnet and live order execution."""
from __future__ import annotations

import abc
import logging
import time
from decimal import Decimal
from typing import Dict, List, Optional

from backend.config import settings
from backend.core.exchange.base import OrderRequest, OrderResponse

logger = logging.getLogger(__name__)


class BaseExecutor(abc.ABC):
    @abc.abstractmethod
    async def execute_order(
        self, symbol: str, side: str, amount: float,
        order_type: str = "market", price=None, stop_price=None
    ) -> Optional[Dict]:
        ...


class PaperExecutor(BaseExecutor):
    """Deterministic local execution simulator.

    Paper mode never calls an exchange. Market orders fill immediately at the
    supplied price; limit/trigger orders remain open until ``process_price``
    is called. The simulator tracks cash, open positions, realized PnL and
    fees, which makes it suitable for restart/recovery and stress tests.
    """

    def __init__(self, virtual_balance: float = 10_000.0, fee_rate: float = 0.0004):
        initial_balance = Decimal(str(virtual_balance))
        normalized_fee_rate = Decimal(str(fee_rate))
        if initial_balance <= 0:
            raise ValueError("virtual_balance must be > 0")
        if normalized_fee_rate < 0:
            raise ValueError("fee_rate must be >= 0")
        self.initial_balance = initial_balance
        self.virtual_balance = self.initial_balance
        self.fee_rate = normalized_fee_rate
        self.open_positions: Dict[str, Dict] = {}
        self.open_orders: Dict[str, Dict] = {}
        self.closed_trades: List[Dict] = []
        self._counter = 0

    def _new_id(self, prefix: str) -> str:
        self._counter += 1
        return f"paper_{prefix}_{self._counter}"

    @staticmethod
    def _is_long(side: str) -> bool:
        return side.lower() == "buy"

    @staticmethod
    def _decimal_string(value: Decimal) -> str:
        return format(Decimal(value), "f")

    @classmethod
    def _serialize_value(cls, value):
        if isinstance(value, Decimal):
            return cls._decimal_string(value)
        if isinstance(value, dict):
            return {key: cls._serialize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._serialize_value(item) for item in value]
        return value

    @staticmethod
    def _restore_value(value):
        if isinstance(value, dict):
            return {key: PaperExecutor._restore_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [PaperExecutor._restore_value(item) for item in value]
        return value

    def _apply_fill(self, order: Dict, fill_price: Decimal) -> Dict:
        qty = Decimal(str(order["amount"]))
        side = order["side"]
        fee = qty * fill_price * self.fee_rate
        self.virtual_balance -= fee
        order["status"] = "closed"
        order["filled"] = qty
        order["avg_price"] = fill_price
        order["fee"] = fee

        # Paper futures are modeled as a single net position per symbol.
        pos = self.open_positions.get(order["symbol"])
        signed = qty if self._is_long(side) else -qty
        if pos is None:
            self.open_positions[order["symbol"]] = {
                "symbol": order["symbol"],
                "side": "buy" if signed > 0 else "sell",
                "quantity": qty,
                "entry_price": fill_price,
                "entry_fee": fee,
                "opened_at": time.time(),
            }
        else:
            old_signed = pos["quantity"] if pos["side"] == "buy" else -pos["quantity"]

            if (old_signed > 0) == (signed > 0):
                # Same-direction add: weighted-average the entry.
                total = abs(old_signed) + abs(signed)
                pos["entry_price"] = (
                    pos["entry_price"] * abs(old_signed) + fill_price * abs(signed)
                ) / total
                pos["quantity"] = total
                pos["entry_fee"] += fee
            else:
                # Opposite-direction order closes exposure first.
                close_qty = min(abs(old_signed), abs(signed))
                if pos["side"] == "buy":
                    pnl = (fill_price - pos["entry_price"]) * close_qty
                else:
                    pnl = (pos["entry_price"] - fill_price) * close_qty
                self.virtual_balance += pnl

                self.closed_trades.append({
                    "symbol": order["symbol"],
                    "side": pos["side"],
                    "quantity": close_qty,
                    "entry_price": pos["entry_price"],
                    "exit_price": fill_price,
                    "pnl": pnl,
                    "fee": fee,
                    "closed_at": time.time(),
                })

                remainder = abs(signed) - close_qty
                if remainder <= 0:
                    pos["quantity"] = abs(old_signed) - close_qty
                    if pos["quantity"] <= 0:
                        self.open_positions.pop(order["symbol"], None)
                else:
                    # The incoming order reverses the remaining exposure.
                    self.open_positions[order["symbol"]] = {
                        "symbol": order["symbol"],
                        "side": "buy" if signed > 0 else "sell",
                        "quantity": remainder,
                        "entry_price": fill_price,
                        "entry_fee": Decimal("0"),
                        "opened_at": time.time(),
                    }
        return order

    async def execute_order(
        self, symbol, side, amount, order_type="market", price=None, stop_price=None
    ):
        qty = Decimal(str(amount))
        if qty <= 0:
            raise ValueError("amount must be > 0")
        self._counter += 1
        order = {
            "status": "open",
            "order_id": f"paper_{self._counter}",
            "symbol": symbol,
            "side": side.lower(),
            "amount": qty,
            "type": order_type.lower(),
            "price": Decimal(str(price)) if price is not None else None,
            "stop_price": Decimal(str(stop_price)) if stop_price is not None else None,
            "created_at": time.time(),
            "filled": Decimal("0"),
            "avg_price": None,
            "fee": Decimal("0"),
        }
        if order["type"] == "market":
            fill_price = Decimal(str(price)) if price is not None else None
            if fill_price is None or fill_price <= 0:
                raise ValueError("Paper market orders require a positive price")
            return self._apply_fill(order, fill_price)
        self.open_orders[order["order_id"]] = order
        return order

    async def process_price(self, symbol: str, price: float) -> List[Dict]:
        """Fill eligible paper limit/trigger orders at a supplied price."""
        px = Decimal(str(price))
        filled = []
        for oid, order in list(self.open_orders.items()):
            if order["symbol"] != symbol:
                continue
            target = order["stop_price"] if order["type"] in {"stop_loss", "take_profit"} else order["price"]
            if target is None:
                continue
            target = Decimal(str(target))
            side = order["side"]
            eligible = (
                px <= target if side == "buy" and order["type"] == "limit"
                else px >= target if side == "sell" and order["type"] == "limit"
                else px <= target if side == "sell" and order["type"] == "stop_loss"
                else px >= target if side == "buy" and order["type"] == "stop_loss"
                else px >= target if side == "sell" and order["type"] == "take_profit"
                else px <= target
            )
            if eligible:
                self._apply_fill(order, target)
                self.open_orders.pop(oid, None)
                filled.append(order)
        return filled

    def mark_to_market(self, symbol: str, price: float) -> Decimal:
        pos = self.open_positions.get(symbol)
        if not pos:
            return self.virtual_balance
        px = Decimal(str(price))
        if pos["side"] == "buy":
            unrealized = (px - pos["entry_price"]) * pos["quantity"]
        else:
            unrealized = (pos["entry_price"] - px) * pos["quantity"]
        return self.virtual_balance + unrealized

    def snapshot(self) -> Dict:
        """Return a JSON-safe canonical snapshot without mutating live state."""
        return self._serialize_value({
            "virtual_balance": self.virtual_balance,
            "open_positions": self.open_positions,
            "open_orders": self.open_orders,
            "closed_trades": self.closed_trades,
            "counter": self._counter,
        })

    def restore(self, payload: Dict) -> None:
        """Restore a snapshot produced by :meth:`snapshot` exactly."""
        if not isinstance(payload, dict):
            raise ValueError("PaperExecutor snapshot must be a mapping")
        restored = self._restore_value(payload)
        self.virtual_balance = Decimal(str(restored.get("virtual_balance", self.initial_balance)))
        self.open_positions = {
            symbol: {
                **position,
                "quantity": Decimal(str(position["quantity"])),
                "entry_price": Decimal(str(position["entry_price"])),
                "entry_fee": Decimal(str(position.get("entry_fee", "0"))),
            }
            for symbol, position in (restored.get("open_positions") or {}).items()
        }
        self.open_orders = {
            order_id: {
                **order,
                "amount": Decimal(str(order["amount"])),
                "price": Decimal(str(order["price"])) if order.get("price") is not None else None,
                "stop_price": Decimal(str(order["stop_price"])) if order.get("stop_price") is not None else None,
                "filled": Decimal(str(order.get("filled", "0"))),
                "avg_price": Decimal(str(order["avg_price"])) if order.get("avg_price") is not None else None,
                "fee": Decimal(str(order.get("fee", "0"))),
            }
            for order_id, order in (restored.get("open_orders") or {}).items()
        }
        self.closed_trades = [
            {
                **trade,
                "quantity": Decimal(str(trade["quantity"])),
                "entry_price": Decimal(str(trade["entry_price"])),
                "exit_price": Decimal(str(trade["exit_price"])),
                "pnl": Decimal(str(trade["pnl"])),
                "fee": Decimal(str(trade.get("fee", "0"))),
            }
            for trade in (restored.get("closed_trades") or [])
        ]
        self._counter = int(restored.get("counter", 0))


class LiveExecutor(BaseExecutor):
    def __init__(self, exchange_adapter):
        self.exchange = exchange_adapter

    async def execute_order(self, symbol, side, amount, order_type="market", price=None, stop_price=None):
        request = OrderRequest(
            symbol=symbol,
            side=side.lower(),
            type=order_type.lower(),
            quantity=Decimal(str(amount)),
            price=Decimal(str(price)) if price is not None else None,
            stopPrice=Decimal(str(stop_price)) if stop_price is not None else None,
        )
        try:
            response: OrderResponse = await __import__("asyncio").to_thread(
                self.exchange.create_order, request
            )
            return response.model_dump()
        except Exception as exc:
            logger.error("Live execution failed: %s", exc)
            raise RuntimeError(
                f"Live order execution failed for {symbol}: {exc}"
            ) from exc


class ExecutorManager:
    def __init__(self, exchange_adapter=None):
        self.paper = PaperExecutor()
        self.live = LiveExecutor(exchange_adapter) if exchange_adapter else None

    def get_executor(self) -> BaseExecutor:
        mode = settings.TRADING_MODE.lower().strip()
        if mode in {"testnet", "live"}:
            if not self.live:
                raise RuntimeError(
                    f"{mode} trading requested but no exchange adapter is configured"
                )
            return self.live
        return self.paper
