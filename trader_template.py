"""
IMC Prosperity — Trader Template
=================================
How to add a new product
------------------------
  1. Add its symbol constant and position limit to SYMBOLS / POS_LIMITS.
  2. Write a subclass of ProductTrader and implement get_orders().
  3. Override get_conversions() if the product needs conversion orders.
  4. Register it in PRODUCT_TRADERS at the bottom.
"""

from __future__ import annotations

import json
from typing import Any

from datamodel import (
    Listing, Observation, Order, OrderDepth,
    ProsperityEncoder, Symbol, Trade, TradingState,
)


# ==============================================================================
# SYMBOLS  —  add new ones each round as they are revealed
# ==============================================================================

# Round 0 (Example)
# EMERALDS_SYMBOL = "EMERALDS"
# TOMATOES_SYMBOL = "TOMATOES"

# Round 1
ASH_COATED_OSMIUM = "ASH_COATED_OSMIUM"
INTARIAN_PEPPER_ROOT = "INTARIAN_PEPPER_ROOT"
# ...

# Round 2+


# ==============================================================================
# POSITION LIMITS  —  fill in / update each round
# ==============================================================================

POS_LIMITS: dict[str, int] = {
    # EMERALDS_SYMBOL: 80,
    # TOMATOES_SYMBOL: 80,
    ASH_COATED_OSMIUM: 80,
    INTARIAN_PEPPER_ROOT: 80
}

CONVERSION_LIMIT = 10

LONG, NEUTRAL, SHORT = 1, 0, -1


# ==============================================================================
# LOGGER
# ==============================================================================

class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict[Symbol, list[Order]], conversions: int, trader_data: str) -> None:
        base_length = len(
            self.to_json([
                self.compress_state(state, ""),
                self.compress_orders(orders),
                conversions,
                "",
                "",
            ])
        )
        max_item_length = (self.max_log_length - base_length) // 3
        print(
            self.to_json([
                self.compress_state(state, self.truncate(state.traderData, max_item_length)),
                self.compress_orders(orders),
                conversions,
                self.truncate(trader_data, max_item_length),
                self.truncate(self.logs, max_item_length),
            ])
        )
        self.logs = ""

    def compress_state(self, state: TradingState, trader_data: str) -> list[Any]:
        return [
            state.timestamp,
            trader_data,
            self.compress_listings(state.listings),
            self.compress_order_depths(state.order_depths),
            self.compress_trades(state.own_trades),
            self.compress_trades(state.market_trades),
            state.position,
            self.compress_observations(state.observations),
        ]

    def compress_listings(self, listings: dict[Symbol, Listing]) -> list[list[Any]]:
        return [[l.symbol, l.product, l.denomination] for l in listings.values()]

    def compress_order_depths(self, order_depths: dict[Symbol, OrderDepth]) -> dict[Symbol, list[Any]]:
        return {s: [od.buy_orders, od.sell_orders] for s, od in order_depths.items()}

    def compress_trades(self, trades: dict[Symbol, list[Trade]]) -> list[list[Any]]:
        return [
            [t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
            for arr in trades.values() for t in arr
        ]

    def compress_observations(self, observations: Observation) -> list[Any]:
        conversion_observations = {}
        for product, obs in observations.conversionObservations.items():
            conversion_observations[product] = [
                obs.bidPrice, obs.askPrice, obs.transportFees,
                obs.exportTariff, obs.importTariff, obs.sugarPrice, obs.sunlightIndex,
            ]
        return [observations.plainValueObservations, conversion_observations]

    def compress_orders(self, orders: dict[Symbol, list[Order]]) -> list[list[Any]]:
        return [
            [o.symbol, o.price, o.quantity]
            for arr in orders.values() for o in arr
        ]

    def to_json(self, value: Any) -> str:
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value: str, max_length: int) -> str:
        lo, hi = 0, min(len(value), max_length)
        out = ""
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = value[:mid]
            if len(candidate) < len(value):
                candidate += "..."
            if len(json.dumps(candidate)) <= max_length:
                out = candidate
                lo = mid + 1
            else:
                hi = mid - 1
        return out


logger = Logger()


# ==============================================================================
# PRODUCT TRADER BASE CLASS
# ==============================================================================

class ProductTrader:
    """
    Per-product base class. Subclass this and implement get_orders().

    Attributes available after __init__
    ─────────────────────────────────────
    Order book
        buy_orders / sell_orders     {price: volume}, volumes always positive
        best_bid / best_ask          tightest quotes
        bid_wall / ask_wall          outermost resting orders
        wall_mid                     (bid_wall + ask_wall) / 2 — use as fair value
        total_buy_vol / sell_vol     total depth each side

    Position
        position_limit               from POS_LIMITS
        position                     current signed position
        max_buy_vol / max_sell_vol   remaining capacity; decremented by bid()/ask()

    Persistence
        mem                          dict of this product's saved state from last tick
        new_mem                      write here to persist values to next tick
                                     (namespaced per symbol automatically)

    Methods
        bid(price, volume)           place a buy order, auto-clamped to capacity
        ask(price, volume)           place a sell order, auto-clamped to capacity
        get_orders()                 override in subclass — return {symbol: [orders]}
        get_conversions()            override in subclass if product needs conversions
    """

    def __init__(self, symbol: str, state: TradingState, new_mem: dict) -> None:
        self.symbol  = symbol
        self.state   = state
        self.orders: list[Order] = []

        # Each product gets its own namespace in the shared new_mem dict
        # so products can never clobber each other's persisted state
        new_mem.setdefault(symbol, {})
        self.new_mem = new_mem[symbol]
        self.mem: dict = self._load_mem(new_mem)

        self.position_limit = POS_LIMITS.get(symbol, 0)
        self.position       = state.position.get(symbol, 0)
        self.max_buy_vol    = self.position_limit - self.position
        self.max_sell_vol   = self.position_limit + self.position

        self.buy_orders, self.sell_orders           = self._parse_book()
        self.best_bid, self.best_ask                = self._best_quotes()
        self.bid_wall, self.wall_mid, self.ask_wall = self._walls()
        self.total_buy_vol  = sum(self.buy_orders.values())
        self.total_sell_vol = sum(self.sell_orders.values())

    def _load_mem(self, new_mem: dict) -> dict:
        # Load only this product's slice from the previous tick's traderData
        try:
            if self.state.traderData:
                return json.loads(self.state.traderData).get(self.symbol, {})
        except Exception:
            pass
        return {}

    def _parse_book(self) -> tuple[dict, dict]:
        od: OrderDepth = self.state.order_depths.get(self.symbol, OrderDepth())
        buys  = dict(sorted({p: abs(v) for p, v in od.buy_orders.items()}.items(),  reverse=True))
        sells = dict(sorted({p: abs(v) for p, v in od.sell_orders.items()}.items()))
        return buys, sells

    def _best_quotes(self) -> tuple:
        best_bid = max(self.buy_orders)  if self.buy_orders  else None
        best_ask = min(self.sell_orders) if self.sell_orders else None
        return best_bid, best_ask

    def _walls(self) -> tuple:
        bid_wall = min(self.buy_orders)  if self.buy_orders  else None
        ask_wall = max(self.sell_orders) if self.sell_orders else None
        wall_mid = (bid_wall + ask_wall) / 2 if (bid_wall and ask_wall) else None
        return bid_wall, wall_mid, ask_wall

    def bid(self, price: float, volume: float) -> None:
        vol = min(int(abs(volume)), self.max_buy_vol)
        if vol <= 0:
            return
        self.orders.append(Order(self.symbol, int(price), vol))
        self.max_buy_vol -= vol

    def ask(self, price: float, volume: float) -> None:
        vol = min(int(abs(volume)), self.max_sell_vol)
        if vol <= 0:
            return
        self.orders.append(Order(self.symbol, int(price), -vol))
        self.max_sell_vol -= vol

    def get_orders(self) -> dict[Symbol, list[Order]]:
        """Override this in each subclass."""
        return {self.symbol: self.orders}

    def get_conversions(self) -> int:
        """
        Override this in subclasses that need conversion orders.
        Conversions tell the exchange to buy/sell through the foreign market
        rather than the local order book. Return a positive int to import,
        negative to export. Capped at ±CONVERSION_LIMIT per tick.
        """
        return 0



# ==============================================================================
# PRODUCT TRADERS  —  implement one subclass per product here
# ==============================================================================

# class MyProductTrader(ProductTrader):
#     def __init__(self, state: TradingState, new_mem: dict) -> None:
#         super().__init__(MY_SYMBOL, state, new_mem)
#
#     def get_orders(self) -> dict[Symbol, list[Order]]:
#         # self.mem        — your persisted state from last tick
#         # self.new_mem    — write here to persist to next tick
#         # self.position   — current signed position
#         # self.wall_mid   — fair value estimate
#         # self.best_bid/ask, self.buy/sell_orders — order book
#         # self.bid(price, volume) / self.ask(price, volume)
#         ...
#         return {self.symbol: self.orders}
#
#     def get_conversions(self) -> int:
#         return 0  # override if this product uses the foreign market

# ==============================================================================

class OsmiumTrader(ProductTrader):
        def __init__(self, state: TradingState, new_mem: dict) -> None:
            super().__init__(ASH_COATED_OSMIUM, state, new_mem)
        
        def get_orders(self) -> dict[Symbol, list[Order]]:

            if self.best_bid is None or self.best_ask is None:
                return {self.symbol: self.orders}
            
            mid_price = (self.best_bid + self.best_ask) / 2

            price_history = self.mem.get("History", [])
            price_history.append(mid_price)

            if len(price_history) > 20:
                price_history.pop(0)

            self.new_mem["History"] = price_history

            mean_price = sum(price_history) / len(price_history)

            threshold = .05

            # finds if price is significantly below the mean
            if mid_price < (mean_price - threshold):
                self.bid(self.best_bid, self.max_buy_vol)
                logger.print(f"Buy {self.symbol}: Price {mid_price} < Mean {mean_price} ")
                # changed the below code to a plus instead of minus
                # it finds if price is significantly above the mean
            elif mid_price > (mean_price + threshold):
                self.ask(self.best_ask, self.max_sell_vol)
                logger.print(f"Sell {self.symbol}: Price {mid_price} > Mean {mean_price}")

            return {self.symbol: self.orders}
        
class PepperRootTrader(ProductTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(INTARIAN_PEPPER_ROOT, state, new_mem)

    def get_orders(self) -> dict[Symbol, list[Order]]:
        if self.best_ask is None:
            return {self.symbol: self.orders}

        # If we still have room to buy, keep buying
        if self.max_buy_vol > 0:
            self.bid(self.best_ask, self.max_buy_vol)
            logger.print(f"Buy and Hold {self.symbol}: position={self.position}/{self.position_limit} at {self.best_ask}")

        mid_price = (self.best_bid + self.best_ask) / 2

        price_history = self.mem.get("History", [])
        price_history.append(mid_price)

        if len(price_history) > 20:
            price_history.pop(0)

        self.new_mem["History"] = price_history

        mean_price = sum(price_history) / len(price_history)
        variance = sum((x - mean_price) ** 2 for x in price_history) / len(price_history)
        std_dev = max(variance ** 1/2, 0.0001)
        z_score = (mid_price - mean_price) / std_dev

        current_pos = self.state.position.get(self.symbol, 0)

        target_pos = int(max(min(-z_score * (self.position_limit / 3), self.position_limit), -self.position_limit))

        pos_diff = target_pos - current_pos

        if pos_diff < 0:
            sell_price = self.best_ask - 1 if self.best_ask - 1 > self.best_bid else self.best_ask
            self.ask(sell_price, abs(pos_diff))
            logger.print(f"Scaling SELL: Z={z_score}, Target={target_pos}, Order={abs(pos_diff)}")

        return {self.symbol: self.orders}

# ==============================================================================
# PRODUCT REGISTRY  —  register active traders here
# ==============================================================================

PRODUCT_TRADERS: dict[str, type[ProductTrader]] = {
    # EMERALDS_SYMBOL: EmeraldsTrader,
    # TOMATOES_SYMBOL: TomatoesTrader,
    ASH_COATED_OSMIUM: OsmiumTrader,
    INTARIAN_PEPPER_ROOT: PepperRootTrader
}


# ==============================================================================
# MAIN TRADER
# ==============================================================================

class Trader:
    def run(
        self, state: TradingState
    ) -> tuple[dict[Symbol, list[Order]], int, str]:

        new_mem:     dict = {}
        result:      dict[Symbol, list[Order]] = {}
        conversions  = 0

        for symbol, TraderClass in PRODUCT_TRADERS.items():
            if symbol not in state.order_depths:
                continue
            try:
                trader = TraderClass(state, new_mem)
                result.update(trader.get_orders())
                conversions += trader.get_conversions()
            except Exception as exc:
                logger.print(f"ERROR [{symbol}]: {exc}")

        trader_data = json.dumps(new_mem)
        logger.flush(state, result, conversions, trader_data)
        return result, conversions, trader_data
