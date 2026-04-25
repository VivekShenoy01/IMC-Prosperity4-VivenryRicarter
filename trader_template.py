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

# Round 3
HYDROGEL_PACK = "HYDROGEL_PACK"
VELVETFRUIT_EXTRACT = "VELVETFRUIT_EXTRACT"
VEV_4000 = "VEV_4000"
VEV_4500 = "VEV_4500"
VEV_5000 = "VEV_5000"
VEV_5100 = "VEV_5100"
VEV_5200 = "VEV_5200"
VEV_5300 = "VEV_5300"
VEV_5400 = "VEV_5400"
VEV_5500 = "VEV_5500"
VEV_6000 = "VEV_6000"
VEV_6500 = "VEV_6500"


# ==============================================================================
# POSITION LIMITS  —  fill in / update each round
# ==============================================================================

POS_LIMITS: dict[str, int] = {
    # EMERALDS_SYMBOL: 80,
    # TOMATOES_SYMBOL: 80,
    HYDROGEL_PACK: 80,
    VELVETFRUIT_EXTRACT: 80,
    VEV_4000: 80,
    VEV_4500: 80,
    VEV_5000: 80,
    VEV_5100: 80,
    VEV_5200: 80,
    VEV_5300: 80,
    VEV_5400: 80,
    VEV_5500: 80,
    VEV_6000: 80,
    VEV_6500: 80,
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


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def get_mid_from_depth(state: TradingState, symbol: str) -> float | None:
    depth = state.order_depths.get(symbol)
    if depth is None or not depth.buy_orders or not depth.sell_orders:
        return None
    return (max(depth.buy_orders) + min(depth.sell_orders)) / 2



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

class HydrogelTrader(ProductTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(HYDROGEL_PACK, state, new_mem)

    def get_orders(self) -> dict[Symbol, list[Order]]:
        if self.best_bid is None or self.best_ask is None:
            return {self.symbol: self.orders}

        mid_price = (self.best_bid + self.best_ask) / 2
        history = self.mem.get("history", [])
        history.append(mid_price)

        window = 30
        if len(history) > window:
            history.pop(0)
        self.new_mem["history"] = history

        if len(history) < 12:
            return {self.symbol: self.orders}

        mean_price = sum(history) / len(history)
        fair_value = round(mean_price)
        spread = self.best_ask - self.best_bid
        inventory_ratio = self.position / max(self.position_limit, 1)

        buy_quote = fair_value - 2
        sell_quote = fair_value + 2

        # Lean quotes away from current inventory so we naturally mean-revert.
        if inventory_ratio > 0.25:
            buy_quote -= 1
            sell_quote -= 1
        elif inventory_ratio < -0.25:
            buy_quote += 1
            sell_quote += 1

        buy_quote = min(buy_quote, self.best_ask - 1)
        buy_quote = max(buy_quote, self.best_bid)
        sell_quote = max(sell_quote, self.best_bid + 1)
        sell_quote = min(sell_quote, self.best_ask)

        base_size = 12
        buy_size = min(base_size, self.max_buy_vol)
        sell_size = min(base_size, self.max_sell_vol)

        # Quote both sides when possible.
        if buy_quote < self.best_ask and buy_size > 0:
            self.bid(buy_quote, buy_size)
        if sell_quote > self.best_bid and sell_size > 0:
            self.ask(sell_quote, sell_size)

        # If the market is far from fair, take a little extra directional size.
        if mid_price <= fair_value - 6 and self.max_buy_vol > 0:
            self.bid(min(self.best_ask - 1, fair_value - 1), min(8, self.max_buy_vol))
        elif mid_price >= fair_value + 6 and self.max_sell_vol > 0:
            self.ask(max(self.best_bid + 1, fair_value + 1), min(8, self.max_sell_vol))

        logger.print(
            f"{self.symbol}: mid={mid_price:.1f} fair={fair_value} spread={spread} "
            f"pos={self.position} buy_q={buy_quote} sell_q={sell_quote}"
        )
        return {self.symbol: self.orders}


class VelvetExtractTrader(ProductTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(VELVETFRUIT_EXTRACT, state, new_mem)

    def get_orders(self) -> dict[Symbol, list[Order]]:
        if self.best_bid is None or self.best_ask is None:
            return {self.symbol: self.orders}

        mid_price = (self.best_bid + self.best_ask) / 2
        history = self.mem.get("history", [])
        history.append(mid_price)

        window = 50
        if len(history) > window:
            history.pop(0)
        self.new_mem["history"] = history

        if len(history) < 20:
            return {self.symbol: self.orders}

        mean_price = sum(history) / len(history)
        variance = sum((price - mean_price) ** 2 for price in history) / len(history)
        std_dev = max(variance ** 0.5, 0.01)
        z_score = (mid_price - mean_price) / std_dev

        short_window = min(10, len(history))
        short_mean = sum(history[-short_window:]) / short_window
        trend = short_mean - mean_price
        last_move = history[-1] - history[-2]

        entry_z = 1.9
        exit_z = 0.5
        aggressiveness = 0.20

        if z_score <= -entry_z and trend >= -0.3 and last_move >= 0:
            target_pos = int(clamp(-z_score * aggressiveness, 0, 1) * self.position_limit)
        elif z_score >= entry_z and trend <= 0.3 and last_move <= 0:
            target_pos = -int(clamp(z_score * aggressiveness, 0, 1) * self.position_limit)
        elif abs(z_score) <= exit_z:
            target_pos = 0
        else:
            target_pos = int(self.position * 0.5)

        pos_diff = target_pos - self.position
        if pos_diff > 0:
            buy_price = self.best_bid + 1
            buy_price = min(buy_price, self.best_ask - 1)
            buy_price = max(buy_price, self.best_bid)
            self.bid(buy_price, pos_diff)
        elif pos_diff < 0:
            sell_price = self.best_ask - 1
            sell_price = max(sell_price, self.best_bid + 1)
            sell_price = min(sell_price, self.best_ask)
            self.ask(sell_price, -pos_diff)

        logger.print(
            f"{self.symbol}: mid={mid_price:.1f} mean={mean_price:.1f} short={short_mean:.1f} "
            f"z={z_score:.2f} trend={trend:.2f} pos={self.position} target={target_pos}"
        )
        return {self.symbol: self.orders}


class VelvetVoucherTrader(ProductTrader):
    def __init__(self, symbol: str, strike: int, state: TradingState, new_mem: dict) -> None:
        super().__init__(symbol, state, new_mem)
        self.strike = strike

    def get_orders(self) -> dict[Symbol, list[Order]]:
        # The simple historical-premium model overvalues decaying OTM vouchers
        # on round 3 data, so keep vouchers disabled until we replace it.
        logger.print(f"{self.symbol}: vouchers disabled")
        return {self.symbol: self.orders}


class VEV4000Trader(VelvetVoucherTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(VEV_4000, 4000, state, new_mem)


class VEV4500Trader(VelvetVoucherTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(VEV_4500, 4500, state, new_mem)


class VEV5000Trader(VelvetVoucherTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(VEV_5000, 5000, state, new_mem)


class VEV5100Trader(VelvetVoucherTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(VEV_5100, 5100, state, new_mem)


class VEV5200Trader(VelvetVoucherTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(VEV_5200, 5200, state, new_mem)


class VEV5300Trader(VelvetVoucherTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(VEV_5300, 5300, state, new_mem)


class VEV5400Trader(VelvetVoucherTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(VEV_5400, 5400, state, new_mem)


class VEV5500Trader(VelvetVoucherTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(VEV_5500, 5500, state, new_mem)


class VEV6000Trader(VelvetVoucherTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(VEV_6000, 6000, state, new_mem)


class VEV6500Trader(VelvetVoucherTrader):
    def __init__(self, state: TradingState, new_mem: dict) -> None:
        super().__init__(VEV_6500, 6500, state, new_mem)

# ==============================================================================
# PRODUCT REGISTRY  —  register active traders here
# ==============================================================================

PRODUCT_TRADERS: dict[str, type[ProductTrader]] = {
    # EMERALDS_SYMBOL: EmeraldsTrader,
    # TOMATOES_SYMBOL: TomatoesTrader,
    HYDROGEL_PACK: HydrogelTrader,
    VELVETFRUIT_EXTRACT: VelvetExtractTrader,
    VEV_4000: VEV4000Trader,
    VEV_4500: VEV4500Trader,
    VEV_5000: VEV5000Trader,
    VEV_5100: VEV5100Trader,
    VEV_5200: VEV5200Trader,
    VEV_5300: VEV5300Trader,
    VEV_5400: VEV5400Trader,
    VEV_5500: VEV5500Trader,
    VEV_6000: VEV6000Trader,
    VEV_6500: VEV6500Trader,
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
