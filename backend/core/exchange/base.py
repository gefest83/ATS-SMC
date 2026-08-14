"""Exchange-neutral contracts used by the trading engine."""
from __future__ import annotations

import abc
import inspect
import logging
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.config import settings

logger = logging.getLogger(__name__)


class OrderBookEntry(BaseModel):
    price: Decimal
    amount: Decimal


class MarketData(BaseModel):
    symbol: str
    timestamp: int
    price: Decimal
    volume: Decimal = Decimal("0")
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    spread: Optional[Decimal] = None
    exchange: Optional[str] = None


class OrderRequest(BaseModel):
    symbol: str
    side: str
    type: str
    quantity: Decimal
    price: Optional[Decimal] = None
    stopPrice: Optional[Decimal] = None
    timeInForce: Optional[str] = "GTC"
    exchange_specific: Optional[Dict[str, Any]] = None
    client_order_id: Optional[str] = None


class OrderResponse(BaseModel):
    order_id: str
    client_order_id: Optional[str] = None
    status: str
    filled_quantity: Decimal = Decimal("0")
    avg_price: Optional[Decimal] = None
    price: Optional[Decimal] = None
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    symbol: Optional[str] = None
    side: Optional[str] = None
    order_type: Optional[str] = None
    quantity: Optional[Decimal] = None
    created_at: float = Field(default_factory=time.time)
    remaining_quantity: Optional[Decimal] = None
    fee_cost: Optional[Decimal] = None
    fee_currency: Optional[str] = None
    fee_items: List[tuple[Decimal, str]] = Field(default_factory=list)
    fill_delta: Decimal = Decimal("0")


class PositionData(BaseModel):
    symbol: str
    exchange: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal
    mark_price: Decimal
    timestamp: int


class OrderStatusUpdate(BaseModel):
    order_id: str
    status: str
    filled_quantity: Decimal
    avg_price: Optional[Decimal] = None
    exchange: str
    symbol: str


class Exchange(abc.ABC):
    @staticmethod
    def decimal_value(value: Any, default: str = "0") -> Decimal:
        """Convert CCXT scalars through text to avoid binary float contamination."""
        if value is None:
            return Decimal(default)
        return value if isinstance(value, Decimal) else Decimal(str(value))

    @classmethod
    def ticker_market_data(cls, ticker: Dict[str, Any], symbol: str) -> MarketData:
        """Normalize a CCXT ticker, computing spread only after Decimal conversion."""
        last = cls.decimal_value(ticker.get("last"))
        bid = cls.decimal_value(ticker.get("bid"), str(last))
        ask = cls.decimal_value(ticker.get("ask"), str(last))
        return MarketData(
            symbol=symbol,
            timestamp=int(ticker.get("timestamp") or 0),
            price=last,
            volume=cls.decimal_value(ticker.get("baseVolume")),
            bid=bid,
            ask=ask,
            spread=ask - bid,
        )

    @staticmethod
    def extract_fee(result: Dict[str, Any]) -> tuple[Optional[Decimal], Optional[str], List[tuple[Decimal, str]]]:
        """Extract CCXT fee information from either ``fee`` or ``fees``.

        CCXT may return one fee object or a list of fee objects depending on
        the exchange and endpoint.  We only aggregate numeric fee costs; the
        currency is preserved so callers can decide whether it is quote/settle
        currency before subtracting it from PnL.
        """
        raw = result.get("fee")
        if raw is None:
            raw = result.get("fees")
        if raw is None:
            return None, None, []
        fees = raw if isinstance(raw, list) else [raw]
        total = Decimal("0")
        currency = None
        found = False
        currencies = set()
        fee_items: List[tuple[Decimal, str]] = []
        for item in fees:
            if not isinstance(item, dict) or item.get("cost") is None:
                continue
            try:
                cost = Decimal(str(item["cost"]))
                fee_currency = str(item.get("currency") or "").upper()
                total += cost
                fee_items.append((cost, fee_currency))
                found = True
                if item.get("currency"):
                    currencies.add(str(item["currency"]).upper())
            except Exception:
                continue
        if not found:
            return None, None, []
        if len(currencies) == 1:
            currency = next(iter(currencies))
        elif len(currencies) > 1:
            # Keep the legacy aggregate fields, but never use a comma-joined
            # currency as if it represented one fee. Callers should use items.
            currency = None
        return total, currency, fee_items

    @classmethod
    def normalize_order_response(
        cls, result: Dict[str, Any], *, symbol: Optional[str] = None,
        request: Optional[OrderRequest] = None, previous_filled: Decimal = Decimal("0")
    ) -> OrderResponse:
        fee_cost, fee_currency, fee_items = cls.extract_fee(result)
        filled = Decimal(str(result.get("filled") or 0))
        fill_delta = max(Decimal("0"), filled - previous_filled)
        average = result.get("average")
        if average is None and result.get("price") is not None and filled > 0:
            average = result.get("price")
        amount = result.get("amount")
        raw_info = result.get("info") if isinstance(result.get("info"), dict) else {}
        client_order_id = result.get("clientOrderId")
        if not client_order_id:
            for key in ("clientOrderId", "newClientOrderId", "orderLinkId", "clOrdId", "clientOid", "text"):
                value = raw_info.get(key)
                if value:
                    client_order_id = str(value)
                    break
        return OrderResponse(
            order_id=str(result.get("id") or ""),
            client_order_id=client_order_id,
            status=str(result.get("status") or "open").lower(),
            filled_quantity=filled,
            fill_delta=fill_delta,
            avg_price=Decimal(str(average)) if average is not None else None,
            price=Decimal(str(result["price"])) if result.get("price") is not None else (request.price if request else None),
            timestamp=int(result.get("timestamp") or int(time.time() * 1000)),
            symbol=result.get("symbol") or (request.symbol if request else symbol),
            side=result.get("side") or (request.side if request else None),
            order_type=result.get("type") or (request.type if request else None),
            quantity=Decimal(str(amount)) if amount is not None else (request.quantity if request else None),
            remaining_quantity=Decimal(str(result["remaining"])) if result.get("remaining") is not None else None,
            fee_cost=fee_cost,
            fee_currency=fee_currency,
            fee_items=fee_items,
        )


    @staticmethod
    def market_options(market_type: str, exchange_name: Optional[str] = None) -> Dict[str, Any]:
        market_type = market_type.lower().strip()
        if market_type != "futures":
            return {"defaultType": "spot"}
        # CCXT uses different default derivative market names across exchanges.
        exchange_name = (exchange_name or settings.EXCHANGE).lower().strip()
        derivative_type = {
            "binance": "future",
            "bybit": "swap",
            "okx": "swap",
            "bitget": "swap",
            "mexc": "swap",
            "kucoin": "swap",
            "gateio": "swap",
        }.get(exchange_name, "swap")
        return {"defaultType": derivative_type, "defaultSubType": "linear"}

    @staticmethod
    def normalize_symbol(symbol: str, market_type: Optional[str] = None) -> str:
        """Normalize a USDT-M symbol to CCXT's unified derivative notation."""
        symbol = str(symbol).strip()
        market_type = (market_type or settings.EXCHANGE_MARKET_TYPE).lower().strip()
        if market_type != "futures" or ":" in symbol:
            return symbol
        settle = settings.FUTURES_SETTLE_ASSET.upper().strip()
        if "/" in symbol:
            base, quote = symbol.split("/", 1)
            if quote.upper() == settle:
                return f"{base}/{quote}:{settle}"
        return symbol

    def client_order_id_param_name(self) -> str:
        """Return the native CCXT/exchange parameter for a client order id.

        CCXT explicitly notes that custom order parameters are exchange-specific.
        Several adapters do not forward the generic ``clientOrderId`` field and
        instead expect their native name.  Using the native key is required for
        ambiguous-submission recovery to work consistently.
        """
        names = {
            "binance": "newClientOrderId",
            "bybit": "orderLinkId",
            "okx": "clOrdId",
            "bitget": "clientOid",
            "mexc": "clientOrderId",
            "kucoin": "clientOid",
            "gateio": "text",
        }
        return names.get(self.get_exchange_name().lower().strip(), "clientOrderId")

    def prepare_order(self, order: OrderRequest) -> tuple[str, str, Optional[str], Dict[str, Any]]:
        """Apply CCXT market precision and derivative-safe trigger parameters."""
        self.validate_order_request(order)
        symbol = self.normalize_symbol(order.symbol)
        ccxt_order_type, normalized_price, params = self.normalize_trigger_order(order)
        self.exchange.load_markets()
        order_type = order.type.lower().strip()
        if order_type in {"stop_loss", "stoploss", "stop", "take_profit", "takeprofit", "tp"}:
            feature_name = "stopLossPrice" if order_type in {"stop_loss", "stoploss", "stop"} else "takeProfitPrice"
            try:
                supported = self.exchange.feature_value(symbol, "createOrder", feature_name)
            except Exception:
                supported = None
            if supported is False:
                raise ValueError(
                    f"{self.get_exchange_name()} does not support standalone {feature_name} for {symbol}"
                )

        # Precision is safety-critical. Never fall back to the unrounded
        # quantity/price: an exchange can reject it or interpret it differently.
        try:
            amount = str(self.exchange.amount_to_precision(symbol, float(order.quantity)))
        except Exception as exc:
            raise ValueError(f"Unable to normalize order amount for {symbol}: {exc}") from exc
        normalized_amount = Decimal(amount)
        if normalized_amount <= 0:
            raise ValueError(f"Order quantity rounds to zero for {symbol}")

        market = getattr(self.exchange, "markets", {}).get(symbol, {})
        limits = market.get("limits") or {}
        amount_limits = limits.get("amount") or {}
        min_amount = amount_limits.get("min")
        max_amount = amount_limits.get("max")
        if min_amount is not None and normalized_amount < Decimal(str(min_amount)):
            raise ValueError(f"Order quantity {normalized_amount} is below minimum {min_amount} for {symbol}")
        if max_amount is not None and normalized_amount > Decimal(str(max_amount)):
            raise ValueError(f"Order quantity {normalized_amount} exceeds maximum {max_amount} for {symbol}")

        if normalized_price is not None:
            try:
                normalized_price = Decimal(
                    str(self.exchange.price_to_precision(symbol, float(normalized_price)))
                )
            except Exception as exc:
                raise ValueError(f"Unable to normalize order price for {symbol}: {exc}") from exc
            if normalized_price <= 0:
                raise ValueError(f"Order price rounds to zero for {symbol}")

            price_limits = limits.get("price") or {}
            min_price = price_limits.get("min")
            max_price = price_limits.get("max")
            if min_price is not None and normalized_price < Decimal(str(min_price)):
                raise ValueError(f"Order price {normalized_price} is below minimum {min_price} for {symbol}")
            if max_price is not None and normalized_price > Decimal(str(max_price)):
                raise ValueError(f"Order price {normalized_price} exceeds maximum {max_price} for {symbol}")

            # CCXT's amount is contracts for contract markets. Convert to
            # notional only when the market explicitly supplies contractSize.
            contract_size = Decimal(str(market.get("contractSize") or 1))
            order_cost = normalized_amount * normalized_price * contract_size
            cost_limits = limits.get("cost") or {}
            min_cost = cost_limits.get("min")
            max_cost = cost_limits.get("max")
            if min_cost is not None and order_cost < Decimal(str(min_cost)):
                raise ValueError(f"Order notional {order_cost} is below minimum {min_cost} for {symbol}")
            if max_cost is not None and order_cost > Decimal(str(max_cost)):
                raise ValueError(f"Order notional {order_cost} exceeds maximum {max_cost} for {symbol}")
        else:
            # Market orders have no price at call time.  Fetch the current
            # ticker to validate notional against the exchange's minimum
            # cost filter (e.g. Binance NOTIONAL).
            cost_limits = limits.get("cost") or {}
            min_cost = cost_limits.get("min")
            if min_cost is not None:
                try:
                    ticker = self.fetch_ticker(symbol)
                    current_price = ticker.price if ticker else None
                except Exception:
                    current_price = None
                if current_price is not None and current_price > 0:
                    contract_size = Decimal(str(market.get("contractSize") or 1))
                    order_cost = normalized_amount * current_price * contract_size
                    if order_cost < Decimal(str(min_cost)):
                        raise ValueError(
                            f"Order notional {order_cost} is below minimum {min_cost} for {symbol}"
                        )

        # A client order id makes an ambiguous submission recoverable: if the
        # exchange accepted the request but the response was lost, recovery can
        # locate the original order instead of creating a duplicate.
        if order.client_order_id:
            params.setdefault(self.client_order_id_param_name(), order.client_order_id)

        market_type = settings.EXCHANGE_MARKET_TYPE.lower().strip()
        if market_type == "futures" and order_type in {"stop_loss", "stoploss", "stop", "take_profit", "takeprofit", "tp"}:
            params.setdefault("reduceOnly", True)
        return symbol, amount, str(normalized_price) if normalized_price is not None else None, params

    def find_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[OrderResponse]:
        """Best-effort recovery for a createOrder whose response was lost.

        Open orders are checked first. If the order has already filled/closed,
        use CCXT's optional fetch_orders endpoint when available. Failure to
        find an order is deliberately not treated as proof that no order was
        accepted.
        """
        normalized_symbol = self.normalize_symbol(symbol)
        try:
            orders = self.fetch_open_orders(symbol)
            for order in orders:
                if order.client_order_id == client_order_id:
                    return order
        except Exception:
            pass

        fetch_orders = getattr(self.exchange, "fetch_orders", None)
        if not callable(fetch_orders):
            return None
        try:
            raw_orders = fetch_orders(normalized_symbol)
        except Exception:
            return None
        for raw in raw_orders or []:
            if not isinstance(raw, dict):
                continue
            raw_info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
            raw_client_id = raw.get("clientOrderId")
            if not raw_client_id:
                for key in ("clientOrderId", "newClientOrderId", "orderLinkId", "clOrdId", "clientOid", "text"):
                    value = raw_info.get(key) or raw.get(key)
                    if value:
                        raw_client_id = str(value)
                        break
            if raw_client_id == client_order_id:
                response = self.normalize_order_response(raw, symbol=symbol)
                # Historical fetches may return an order that already filled,
                # canceled, expired, or was rejected. Such an order must not be
                # reused as an active protective order during recovery.
                terminal = {
                    "closed", "filled", "canceled", "cancelled",
                    "expired", "rejected", "done",
                }
                if str(response.status or "").lower() in terminal:
                    continue
                return response
        return None

    @staticmethod
    def normalize_balance(balance: Dict[str, Any]) -> Dict[str, Decimal]:
        """Normalize CCXT unified balance into {asset: total} safely.

        CCXT balance responses also contain metadata keys (info, timestamp,
        free, used, total). Only currency mappings are returned.
        """
        totals = balance.get("total")
        if isinstance(totals, dict):
            return {
                str(asset): Decimal(str(value))
                for asset, value in totals.items()
                if value is not None
            }

        free = balance.get("free") if isinstance(balance.get("free"), dict) else {}
        used = balance.get("used") if isinstance(balance.get("used"), dict) else {}
        assets = set(free) | set(used)
        result: Dict[str, Decimal] = {}
        for asset in assets:
            free_value = Decimal(str(free.get(asset) or 0))
            used_value = Decimal(str(used.get(asset) or 0))
            result[str(asset)] = free_value + used_value
        return result

    def normalize_position(self, position: Dict[str, Any]) -> Optional[PositionData]:
        """Normalize a CCXT unified position, tolerating exchange-specific info.

        CCXT exposes ``contracts``/``entryPrice``/``markPrice``/
        ``unrealizedPnl`` in the unified position structure; raw exchange
        fields such as ``qty`` are only fallbacks.
        """
        symbol = position.get("symbol")
        if not symbol:
            return None
        contracts = position.get("contracts")
        if contracts is None:
            info = position.get("info") or {}
            contracts = info.get("qty") or info.get("size") or info.get("positionAmt")
        quantity = Decimal(str(contracts or 0))
        if quantity == 0:
            return None
        side = str(position.get("side") or "").lower()
        if side not in {"buy", "sell", "long", "short"}:
            side = "buy" if quantity > 0 else "sell"
        if side == "long":
            side = "buy"
        elif side == "short":
            side = "sell"
        entry = position.get("entryPrice")
        mark = position.get("markPrice") or position.get("lastPrice") or entry
        pnl = position.get("unrealizedPnl")
        info = position.get("info") or {}
        entry = entry if entry is not None else info.get("entryPrice") or info.get("avgEntryPrice")
        mark = mark if mark is not None else info.get("markPrice") or info.get("mark_price")
        pnl = pnl if pnl is not None else info.get("unrealizedPnl") or info.get("unrealisedPnl") or 0
        if entry is None or mark is None:
            return None
        timestamp = position.get("timestamp") or info.get("timestamp") or int(time.time() * 1000)
        return PositionData(
            symbol=symbol,
            exchange=self.get_exchange_name(),
            side=side,
            quantity=abs(quantity),
            entry_price=Decimal(str(entry)),
            unrealized_pnl=Decimal(str(pnl)),
            mark_price=Decimal(str(mark)),
            timestamp=int(timestamp),
        )

    @staticmethod
    def validate_order_request(order: OrderRequest) -> None:
        side = order.side.lower().strip()
        order_type = order.type.lower().strip()
        if side not in {"buy", "sell"}:
            raise ValueError(f"Unsupported order side: {order.side}")
        if order.quantity <= 0:
            raise ValueError("Order quantity must be positive")
        if order_type in {"limit", "stop_loss", "stoploss", "stop", "take_profit", "takeprofit", "tp"}:
            if order_type == "limit" and (order.price is None or order.price <= 0):
                raise ValueError("Limit orders require a positive price")
            if order_type != "limit" and (order.stopPrice is None or order.stopPrice <= 0) and (order.price is None or order.price <= 0):
                raise ValueError("Trigger orders require a positive stopPrice or price")

    @staticmethod
    def normalize_trigger_order(order: OrderRequest) -> tuple[str, Optional[Decimal], Dict[str, Any]]:
        """Return a CCXT-compatible base order type/price/params.

        Trigger prices are deliberately expressed through CCXT's unified
        stopLossPrice/takeProfitPrice parameters instead of exchange-specific
        stop/take-profit order types. Adapters may add their own params.
        """
        params: Dict[str, Any] = dict(order.exchange_specific or {})
        order_type = order.type.lower()
        price = order.price
        if order.timeInForce and order_type == "limit":
            params.setdefault("timeInForce", order.timeInForce)
        if order_type in {"stop_loss", "stoploss", "stop"}:
            params.setdefault("stopLossPrice", str(order.stopPrice))
            return "market", None, params
        if order_type in {"take_profit", "takeprofit", "tp"}:
            params.setdefault("takeProfitPrice", str(order.stopPrice or order.price))
            return "market", None, params
        return order_type, price, params

    @abc.abstractmethod
    def __init__(self, credentials: Dict[str, Any], sandbox: bool = False): ...

    @abc.abstractmethod
    def fetch_balance(self) -> Dict[str, Decimal]: ...

    @abc.abstractmethod
    def fetch_ticker(self, symbol: str) -> MarketData: ...

    @abc.abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 200) -> list: ...

    @abc.abstractmethod
    def fetch_order_book(self, symbol: str, limit: int = 20) -> List[OrderBookEntry]: ...

    @abc.abstractmethod
    def create_order(self, order: OrderRequest) -> OrderResponse: ...

    @abc.abstractmethod
    def fetch_order(self, symbol: str, order_id: str) -> OrderResponse: ...

    @abc.abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> bool: ...

    @abc.abstractmethod
    def fetch_open_orders(self, symbol: Optional[str] = None) -> List[OrderResponse]: ...

    @abc.abstractmethod
    def fetch_positions(self, symbol: Optional[str] = None) -> List[PositionData]: ...

    def fetch_my_trades(
        self,
        symbol: Optional[str] = None,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return normalized CCXT private fills for reconciliation.

        This is intentionally a concrete method because CCXT exposes the same
        unified ``fetch_my_trades`` contract across supported adapters.  The
        method keeps the raw unified trade dictionaries so exchange-specific
        metadata remains available for audit/recovery.
        """
        if not hasattr(self, "exchange") or not hasattr(self.exchange, "fetch_my_trades"):
            raise NotImplementedError(f"{self.get_exchange_name()} does not support fetch_my_trades")
        normalized_symbol = self.normalize_symbol(symbol) if symbol else None
        kwargs: Dict[str, Any] = {}
        if limit is not None:
            kwargs["limit"] = int(limit)
        return self.exchange.fetch_my_trades(normalized_symbol, since=since, **kwargs)

    @abc.abstractmethod
    def get_wallet_balance(self, asset: str) -> Decimal: ...

    @abc.abstractmethod
    def get_exchange_name(self) -> str: ...

    async def _dispatch_callback(self, callback, payload) -> None:
        result = callback(payload)
        if inspect.isawaitable(result):
            await result

    async def close(self) -> None:
        """Close REST and WebSocket clients without leaking event-loop resources."""
        clients = []
        for attribute in ("pro", "exchange"):
            client = getattr(self, attribute, None)
            if client is not None and client not in clients:
                clients.append(client)

        for client in clients:
            close = getattr(client, "close", None)
            if not callable(close):
                continue
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Failed to close %s client", type(client).__name__)

        if hasattr(self, "pro"):
            self.pro = None

    async def watch_ticker(self, symbol: str, callback=None) -> MarketData:
        """Return exactly one ticker snapshot for one await.

        Adapters may invoke the optional callback for legacy callers, but the
        stream owner is responsible for looping and handling reconnects.
        """
        raise NotImplementedError

    async def watch_ohlcv(self, symbol: str, timeframe: str, callback=None):
        """Return one OHLCV snapshot for one await.

        The caller owns looping, reconnects, and cancellation.  ``callback``
        is optional compatibility for callers that still consume callbacks.
        """
        raise NotImplementedError

    async def watch_order_book(self, symbol: str, callback=None):
        """Return one order-book snapshot for one await."""
        raise NotImplementedError

    async def watch_positions(self, callback=None):
        """Return one positions snapshot for one await."""
        raise NotImplementedError

    async def watch_orders(self, callback=None):
        """Return one open-orders snapshot for one await."""
        raise NotImplementedError
