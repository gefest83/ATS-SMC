"""Scores an SMC market snapshot and turns it into a directional signal."""
from decimal import Decimal
from typing import Dict, Optional


class SignalGenerator:
    def __init__(self, min_rr: float = 2.0, score_threshold: int = 4, ob_proximity: float = 0.005):
        self.min_rr = Decimal(str(min_rr))
        self.score_threshold = score_threshold
        self.ob_proximity = Decimal(str(ob_proximity))

    def score(self, analysis: Dict) -> int:
        price = analysis["current_price"]
        score = 0

        for fvg in analysis["fvgs"]:
            if fvg["type"] == "BULLISH" and price <= fvg["top"]:
                score += 2
            elif fvg["type"] == "BEARISH" and price >= fvg["bottom"]:
                score -= 2

        for ob in analysis["order_blocks"]:
            if abs(price - ob["price"]) / price > self.ob_proximity:
                continue
            score += 3 if ob["type"] == "BULLISH" else -3

        trend = analysis["structure"].get("trend") or []
        if trend:
            score += 1 if trend[0]["type"] == "BULLISH" else -1

        return score

    def generate_signal(self, analysis: Optional[Dict]) -> Optional[str]:
        if not analysis:
            return None

        score = self.score(analysis)
        if score >= self.score_threshold:
            return "BUY"
        if score <= -self.score_threshold:
            return "SELL"
        return None

    def build_levels(self, analysis: Dict, side: str) -> Dict[str, Decimal]:
        """Build precise ATR-based entry, stop-loss, and three take-profits."""
        price = analysis["current_price"]
        price = price if isinstance(price, Decimal) else Decimal(str(price))
        raw_atr = analysis.get("atr")
        atr_value = raw_atr if isinstance(raw_atr, Decimal) else Decimal(str(raw_atr or "0"))
        distance = atr_value or price * Decimal("0.005")
        side = side.upper()
        # Keep each staged exit strictly between entry and the final target.
        # For a configured target below 3R, divide that target into three
        # increasing stages; for 3R and above, retain the conventional 1R/2R
        # early exits and honor the configured final R:R exactly.
        if self.min_rr < Decimal("3"):
            tp_multiples = (
                self.min_rr / Decimal("3"),
                self.min_rr * Decimal("2") / Decimal("3"),
                self.min_rr,
            )
        else:
            tp_multiples = (Decimal("1"), Decimal("2"), self.min_rr)

        if side == "BUY":
            stop_loss = price - distance
            tp1 = price + distance * tp_multiples[0]
            tp2 = price + distance * tp_multiples[1]
            tp3 = price + distance * tp_multiples[2]
        else:
            stop_loss = price + distance
            tp1 = price - distance * tp_multiples[0]
            tp2 = price - distance * tp_multiples[1]
            tp3 = price - distance * tp_multiples[2]
        return {
            "entry": price,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "take_profit": tp3,
        }
