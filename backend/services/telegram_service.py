"""
Telegram Notification Service for ATS-SMT Pro Trading Bot.

Sends trading notifications to Telegram with throttling and deduplication.
All messages in Russian language.
"""
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from enum import Enum

logger = logging.getLogger(__name__)


class MessageType(Enum):
    """Types of Telegram notifications."""
    BOT_STARTED = "BOT_STARTED"
    BOT_STOPPED = "BOT_STOPPED"
    SIGNAL_LONG = "SIGNAL_LONG"
    SIGNAL_SHORT = "SIGNAL_SHORT"
    ORDER_OPENED = "ORDER_OPENED"
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    TP3_HIT = "TP3_HIT"
    STOP_LOSS = "STOP_LOSS"
    BREAKEVEN = "BREAKEVEN"
    CHoCH_EXIT = "CHoCH_EXIT"
    FLIP = "FLIP"
    ERROR = "ERROR"
    WARNING = "WARNING"


class TelegramService:
    """
    Telegram notification service with throttling and deduplication.
    
    Features:
    - All messages in Russian
    - Message throttling (max N messages per minute)
    - Duplicate message suppression
    - Error categorization
    - Async sending
    """
    
    # Throttling settings
    MAX_MESSAGES_PER_MINUTE = 10
    MIN_MESSAGE_INTERVAL_SECONDS = 2
    
    # Deduplication window (seconds)
    DEDUP_WINDOW_SECONDS = 60
    
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        """
        Initialize Telegram service.
        
        Args:
            token: Telegram bot token
            chat_id: Target chat ID for notifications
        """
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        
        # Rate limiting
        self._message_timestamps: List[datetime] = []
        self._last_message_time: Optional[datetime] = None
        
        # Deduplication cache
        self._sent_messages: Dict[str, datetime] = {}
        
        # Error tracking to avoid spam
        self._error_counts: Dict[str, int] = {}
        self._last_error_time: Dict[str, datetime] = {}
        
        if not self.enabled:
            logger.info("Telegram notifications disabled (no token or chat_id)")
        else:
            logger.info(f"Telegram notifications enabled for chat {chat_id}")
    
    def _get_message_hash(self, message_type: MessageType, content: Dict[str, Any]) -> str:
        """Generate hash for deduplication."""
        # Create a unique key based on message type and key content
        key_parts = [message_type.value]
        
        if message_type in [MessageType.SIGNAL_LONG, MessageType.SIGNAL_SHORT]:
            key_parts.extend([
                content.get("symbol", ""),
                content.get("entry", ""),
                content.get("timestamp", ""),
            ])
        elif message_type in [MessageType.TP1_HIT, MessageType.TP2_HIT, MessageType.TP3_HIT, MessageType.STOP_LOSS]:
            key_parts.extend([
                content.get("symbol", ""),
                content.get("price", ""),
            ])
        elif message_type == MessageType.ERROR:
            key_parts.extend([
                content.get("component", ""),
                content.get("error", ""),
            ])
        
        return "|".join(str(p) for p in key_parts)
    
    def _is_duplicate(self, message_hash: str) -> bool:
        """Check if message is duplicate within dedup window."""
        if message_hash in self._sent_messages:
            last_sent = self._sent_messages[message_hash]
            if datetime.utcnow() - last_sent < timedelta(seconds=self.DEDUP_WINDOW_SECONDS):
                return True
        return False
    
    def _record_message(self, message_hash: str):
        """Record message as sent."""
        self._sent_messages[message_hash] = datetime.utcnow()
        
        # Clean old entries
        now = datetime.utcnow()
        self._sent_messages = {
            h: t for h, t in self._sent_messages.items()
            if now - t < timedelta(seconds=self.DEDUP_WINDOW_SECONDS * 2)
        }
    
    async def _check_rate_limit(self) -> bool:
        """Check if we can send a message without exceeding rate limits."""
        now = datetime.utcnow()
        
        # Remove old timestamps
        self._message_timestamps = [
            ts for ts in self._message_timestamps
            if now - ts < timedelta(minutes=1)
        ]
        
        # Check limit
        if len(self._message_timestamps) >= self.MAX_MESSAGES_PER_MINUTE:
            logger.warning("Telegram rate limit reached, skipping message")
            return False
        
        # Check minimum interval
        if self._last_message_time:
            elapsed = (now - self._last_message_time).total_seconds()
            if elapsed < self.MIN_MESSAGE_INTERVAL_SECONDS:
                await asyncio.sleep(self.MIN_MESSAGE_INTERVAL_SECONDS - elapsed)
        
        return True
    
    async def _send_message(self, text: str) -> bool:
        """
        Send message to Telegram API.
        
        Args:
            text: Message text to send
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            logger.debug(f"Telegram message not sent (disabled): {text[:100]}")
            return False
        
        # Check rate limit
        if not await self._check_rate_limit():
            return False
        
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as response:
                    if response.status == 200:
                        self._message_timestamps.append(datetime.utcnow())
                        self._last_message_time = datetime.utcnow()
                        logger.debug(f"Telegram message sent successfully")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Telegram API error: {response.status} - {error_text}")
                        return False
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
    
    async def notify_bot_started(self, config: Dict[str, Any]):
        """Send bot started notification."""
        mode = config.get("trading_mode", "PAPER").upper()
        exchanges = config.get("exchanges", [])
        symbols = config.get("symbols", [])
        timeframe = config.get("timeframe", "30m")
        risk_pct = config.get("risk_pct", 1.0)
        max_trades = config.get("max_open_trades", 3)
        
        text = (
            "🤖 <b>ATS-SMT ЗАПУЩЕН</b>\n\n"
            f"<b>Mode:</b> {mode}\n"
            f"<b>Биржи:</b> {', '.join(exchanges) if exchanges else 'Не указаны'}\n"
            f"<b>Пары:</b> {len(symbols)} символов\n"
            f"<b>Таймфрейм:</b> {timeframe}\n"
            f"<b>Риск:</b> {risk_pct}%\n"
            f"<b>Макс позиций:</b> {max_trades}\n"
            f"<b>Статус:</b> ✅ ГОТОВ К РАБОТЕ"
        )
        
        await self._send_message(text)
    
    async def notify_bot_stopped(self, reason: str, open_positions: int = 0):
        """Send bot stopped notification."""
        text = (
            "🛑 <b>ATS-SMT ОСТАНОВЛЕН</b>\n\n"
            f"<b>Причина:</b> {reason}\n"
            f"<b>Открыто позиций:</b> {open_positions}\n"
            f"<b>Статус:</b> ⏹️ ОСТАНОВЛЕН"
        )
        
        await self._send_message(text)
    
    async def notify_signal(self, signal_data: Dict[str, Any]):
        """Send signal notification (LONG or SHORT)."""
        action = signal_data.get("action", "UNKNOWN")
        symbol = signal_data.get("symbol", "UNKNOWN")
        timeframe = signal_data.get("timeframe", "M30")
        
        entry = signal_data.get("entry", 0)
        sl = signal_data.get("sl", 0)
        tp1 = signal_data.get("tp1", 0)
        tp2 = signal_data.get("tp2", 0)
        tp3 = signal_data.get("tp3", 0)
        
        regime = signal_data.get("regime", "UNKNOWN")
        votes = signal_data.get("votes", 0)
        total_votes = signal_data.get("total_votes", 3)
        
        htf_4h = signal_data.get("htf4h", "NEUTRAL")
        htf_1d = signal_data.get("htf1d", "NEUTRAL")
        adx = signal_data.get("adx", 0)
        risk_pct = signal_data.get("risk_pct", 1.0)
        
        emoji = "🟢" if action == "LONG" else "🔴"
        direction = "LONG" if action == "LONG" else "SHORT"
        
        text = (
            f"{emoji} <b>СИГНАЛ {direction}</b>\n\n"
            f"<b>Пара:</b> {symbol}\n"
            f"<b>TF:</b> {timeframe}\n\n"
            f"<b>Entry:</b> {entry:.4f}\n"
            f"<b>SL:</b> {sl:.4f}\n"
            f"<b>TP1:</b> {tp1:.4f}\n"
            f"<b>TP2:</b> {tp2:.4f}\n"
            f"<b>TP3:</b> {tp3:.4f}\n\n"
            f"<b>Regime:</b> {regime}\n"
            f"<b>Votes:</b> {votes}/{total_votes}\n\n"
            f"<b>HTF 4H:</b> {'⬆️' if htf_4h == 'UP' else '⬇️' if htf_4h == 'DOWN' else '➡️'} {htf_4h}\n"
            f"<b>HTF 1D:</b> {'⬆️' if htf_1d == 'UP' else '⬇️' if htf_1d == 'DOWN' else '➡️'} {htf_1d}\n"
            f"<b>ADX:</b> {adx:.1f}\n"
            f"<b>Risk:</b> {risk_pct}%"
        )
        
        message_type = MessageType.SIGNAL_LONG if action == "LONG" else MessageType.SIGNAL_SHORT
        message_hash = self._get_message_hash(message_type, signal_data)
        
        if self._is_duplicate(message_hash):
            logger.debug(f"Duplicate signal notification suppressed: {symbol} {direction}")
            return
        
        await self._send_message(text)
        self._record_message(message_hash)
    
    async def notify_order_opened(self, order_data: Dict[str, Any]):
        """Send order opened notification."""
        exchange = order_data.get("exchange", "UNKNOWN")
        symbol = order_data.get("symbol", "UNKNOWN")
        side = order_data.get("side", "UNKNOWN")
        price = order_data.get("price", 0)
        quantity = order_data.get("quantity", 0)
        
        sl = order_data.get("sl", 0)
        tp1 = order_data.get("tp1", 0)
        tp2 = order_data.get("tp2", 0)
        tp3 = order_data.get("tp3", 0)
        
        text = (
            "🟢 <b>ОРДЕР ОТКРЫТ</b>\n\n"
            f"<b>Биржа:</b> {exchange}\n"
            f"<b>Пара:</b> {symbol}\n"
            f"<b>Направление:</b> {side}\n"
            f"<b>Цена:</b> {price:.4f}\n"
            f"<b>Количество:</b> {quantity:.6f}\n"
            f"<b>SL:</b> {sl:.4f}\n"
            f"<b>TP1:</b> {tp1:.4f}\n"
            f"<b>TP2:</b> {tp2:.4f}\n"
            f"<b>TP3:</b> {tp3:.4f}"
        )
        
        message_hash = self._get_message_hash(MessageType.ORDER_OPENED, order_data)
        
        if self._is_duplicate(message_hash):
            logger.debug(f"Duplicate order notification suppressed: {symbol} {side}")
            return
        
        await self._send_message(text)
        self._record_message(message_hash)
    
    async def notify_tp_hit(self, symbol: str, tp_level: str, price: float, closed_pct: int, pnl: float):
        """Send TP hit notification."""
        emojis = {"TP1": "💰", "TP2": "💰", "TP3": "🏆"}
        emoji = emojis.get(tp_level, "💰")
        
        text = (
            f"{emoji} <b>{tp_level} ДОСТИГНУТ</b>\n\n"
            f"<b>Пара:</b> {symbol}\n"
            f"<b>Цена:</b> {price:.4f}\n"
            f"<b>Закрыто:</b> {closed_pct}%\n"
            f"<b>PnL:</b> ${pnl:.2f}"
        )
        
        message_type = getattr(MessageType, f"{tp_level}_HIT", MessageType.TP1_HIT)
        content = {"symbol": symbol, "price": price, "tp_level": tp_level}
        message_hash = self._get_message_hash(message_type, content)
        
        if self._is_duplicate(message_hash):
            logger.debug(f"Duplicate TP notification suppressed: {symbol} {tp_level}")
            return
        
        await self._send_message(text)
        self._record_message(message_hash)
    
    async def notify_stop_loss(self, symbol: str, price: float, pnl: float, reason: str = "SL"):
        """Send stop loss notification."""
        text = (
            "🔴 <b>STOP LOSS</b>\n\n"
            f"<b>Пара:</b> {symbol}\n"
            f"<b>Цена:</b> {price:.4f}\n"
            f"<b>PnL:</b> ${pnl:.2f}\n"
            f"<b>Причина:</b> {reason}\n\n"
            f"<b>Cooldown:</b> 6 M30 bars"
        )
        
        content = {"symbol": symbol, "price": price, "reason": reason}
        message_hash = self._get_message_hash(MessageType.STOP_LOSS, content)
        
        if self._is_duplicate(message_hash):
            logger.debug(f"Duplicate SL notification suppressed: {symbol}")
            return
        
        await self._send_message(text)
        self._record_message(message_hash)
    
    async def notify_breakeven(self, symbol: str, new_sl: float):
        """Send breakeven activated notification."""
        text = (
            "🟡 <b>BREAKEVEN АКТИВИРОВАН</b>\n\n"
            f"<b>Пара:</b> {symbol}\n"
            f"<b>Новый SL:</b> {new_sl:.4f}"
        )
        
        content = {"symbol": symbol, "new_sl": new_sl}
        message_hash = self._get_message_hash(MessageType.BREAKEVEN, content)
        
        if self._is_duplicate(message_hash):
            logger.debug(f"Duplicate BE notification suppressed: {symbol}")
            return
        
        await self._send_message(text)
        self._record_message(message_hash)
    
    async def notify_choch_exit(self, symbol: str, pnl: float, reason: str):
        """Send CHoCH/structure exit notification."""
        text = (
            "⚠️ <b>CHoCH / STRUCTURE EXIT</b>\n\n"
            f"<b>Пара:</b> {symbol}\n"
            f"<b>Причина:</b> {reason}\n"
            f"<b>PnL:</b> ${pnl:.2f}\n\n"
            f"Позиция закрыта из-за противоположной структуры."
        )
        
        content = {"symbol": symbol, "reason": reason}
        message_hash = self._get_message_hash(MessageType.CHoCH_EXIT, content)
        
        if self._is_duplicate(message_hash):
            logger.debug(f"Duplicate CHoCH exit notification suppressed: {symbol}")
            return
        
        await self._send_message(text)
        self._record_message(message_hash)
    
    async def notify_flip(self, symbol: str, from_side: str, to_side: str):
        """Send flip notification (LONG→SHORT or SHORT→LONG)."""
        text = (
            "🔄 <b>FLIP</b>\n\n"
            f"<b>Пара:</b> {symbol}\n"
            f"<b>{from_side}</b> → <b>{to_side}</b>"
        )
        
        content = {"symbol": symbol, "from": from_side, "to": to_side}
        message_hash = self._get_message_hash(MessageType.FLIP, content)
        
        if self._is_duplicate(message_hash):
            logger.debug(f"Duplicate flip notification suppressed: {symbol}")
            return
        
        await self._send_message(text)
        self._record_message(message_hash)
    
    async def notify_error(self, component: str, error: str, exchange: Optional[str] = None, symbol: Optional[str] = None):
        """Send error notification with throttling to prevent spam."""
        error_key = f"{component}:{error}"
        now = datetime.utcnow()
        
        # Count errors
        if error_key not in self._error_counts:
            self._error_counts[error_key] = 0
            self._last_error_time[error_key] = now
        
        self._error_counts[error_key] += 1
        count = self._error_counts[error_key]
        
        # Only send first error and then every 10th
        if count > 1 and count % 10 != 0:
            return
        
        # Reset after 5 minutes
        if now - self._last_error_time[error_key] > timedelta(minutes=5):
            self._error_counts[error_key] = 1
            self._last_error_time[error_key] = now
        
        location = ""
        if exchange:
            location += f"<b>Exchange:</b> {exchange}\n"
        if symbol:
            location += f"<b>Symbol:</b> {symbol}\n"
        
        text = (
            "🚨 <b>ATS-SMT ERROR</b>\n\n"
            f"<b>Component:</b> {component}\n"
            f"{location}"
            f"<b>Error:</b> {error}\n\n"
            f"(Сообщение #{count})"
        )
        
        content = {"component": component, "error": error, "exchange": exchange, "symbol": symbol}
        message_hash = self._get_message_hash(MessageType.ERROR, content)
        
        if self._is_duplicate(message_hash):
            return
        
        await self._send_message(text)
        self._record_message(message_hash)
    
    async def notify_warning(self, message: str):
        """Send warning notification."""
        text = f"⚠️ <b>WARNING</b>\n\n{message}"
        await self._send_message(text)


# Singleton instance
_telegram_service: Optional[TelegramService] = None


def get_telegram_service() -> TelegramService:
    """Get or create Telegram service singleton."""
    global _telegram_service
    if _telegram_service is None:
        from backend.config.settings import config
        _telegram_service = TelegramService(
            token=config.telegram_token,
            chat_id=config.telegram_chat_id
        )
    return _telegram_service
