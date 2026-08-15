"""
WebSocket Service for Real-time Dashboard Updates
ATS-SMT PRO Trading System
"""

import asyncio
import json
from datetime import datetime
from typing import Set, Dict, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for real-time updates"""
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.subscriptions: Dict[WebSocket, Set[str]] = {}
        self._lock = asyncio.Lock()
        
    async def connect(self, websocket: WebSocket, channels: Optional[list] = None):
        """Accept connection and optionally subscribe to channels"""
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
            self.subscriptions[websocket] = set(channels or ["*"])
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")
        
    def disconnect(self, websocket: WebSocket):
        """Remove connection"""
        self.active_connections.discard(websocket)
        self.subscriptions.pop(websocket, None)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")
        
    def subscribe(self, websocket: WebSocket, channels: list):
        """Subscribe to specific channels"""
        if websocket in self.subscriptions:
            self.subscriptions[websocket].update(channels)
            
    def unsubscribe(self, websocket: WebSocket, channels: list):
        """Unsubscribe from specific channels"""
        if websocket in self.subscriptions:
            self.subscriptions[websocket] -= set(channels)
            
    def _is_subscribed(self, websocket: WebSocket, channel: str) -> bool:
        """Check if connection is subscribed to channel"""
        if websocket not in self.subscriptions:
            return False
        subs = self.subscriptions[websocket]
        return "*" in subs or channel in subs
        
    async def broadcast(self, channel: str, message: dict):
        """Broadcast message to all subscribers of a channel"""
        payload = json.dumps({
            "channel": channel,
            "timestamp": datetime.utcnow().isoformat(),
            "data": message
        })
        
        disconnected = set()
        async with self._lock:
            for connection in self.active_connections.copy():
                if self._is_subscribed(connection, channel):
                    try:
                        await connection.send_text(payload)
                    except WebSocketDisconnect:
                        disconnected.add(connection)
                    except Exception as e:
                        logger.error(f"Error sending to WebSocket: {e}")
                        disconnected.add(connection)
                        
        # Clean up disconnected
        for conn in disconnected:
            self.disconnect(conn)
            
    async def send_personal(self, websocket: WebSocket, message: dict):
        """Send message to specific connection"""
        try:
            payload = json.dumps({
                "channel": "personal",
                "timestamp": datetime.utcnow().isoformat(),
                "data": message
            })
            await websocket.send_text(payload)
        except Exception as e:
            logger.error(f"Error sending personal message: {e}")
            self.disconnect(websocket)


class WebSocketService:
    """Main WebSocket service for ATS-SMT PRO"""
    
    def __init__(self):
        self.manager = ConnectionManager()
        self._running = False
        self._tasks: Dict[str, asyncio.Task] = {}
        
    async def start(self):
        """Start background tasks"""
        self._running = True
        logger.info("WebSocket Service started")
        
    async def stop(self):
        """Stop all background tasks"""
        self._running = False
        for task_name, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("WebSocket Service stopped")
        
    # Broadcast helpers for different channels
    
    async def broadcast_price(self, symbol: str, price: float, change_24h: float = 0.0):
        """Broadcast price update"""
        await self.manager.broadcast("prices", {
            "symbol": symbol,
            "price": price,
            "change_24h": change_24h
        })
        
    async def broadcast_position(self, position_data: dict):
        """Broadcast position update"""
        await self.manager.broadcast("positions", position_data)
        
    async def broadcast_order(self, order_data: dict):
        """Broadcast order update"""
        await self.manager.broadcast("orders", order_data)
        
    async def broadcast_signal(self, signal_data: dict):
        """Broadcast signal update"""
        await self.manager.broadcast("signals", signal_data)
        
    async def broadcast_risk(self, risk_data: dict):
        """Broadcast risk metrics update"""
        await self.manager.broadcast("risk", risk_data)
        
    async def broadcast_log(self, log_data: dict):
        """Broadcast log entry"""
        await self.manager.broadcast("logs", log_data)
        
    async def broadcast_engine_status(self, status_data: dict):
        """Broadcast engine status update"""
        await self.manager.broadcast("engine", status_data)
        
    async def broadcast_strategy_settings(self, settings_data: dict):
        """Broadcast strategy settings update"""
        await self.manager.broadcast("settings", settings_data)
        
    async def broadcast_market_health(self, health_data: dict):
        """Broadcast market health status"""
        await self.manager.broadcast("health", health_data)
        
    async def broadcast_chart_update(self, chart_data: dict):
        """Broadcast chart data update"""
        await self.manager.broadcast("charts", chart_data)


# Global instance
websocket_service = WebSocketService()


async def websocket_endpoint(websocket: WebSocket, channels: str = "*"):
    """WebSocket endpoint for dashboard connections"""
    channel_list = channels.split(",") if channels != "*" else ["*"]
    await websocket_service.manager.connect(websocket, channel_list)
    
    try:
        while True:
            # Keep connection alive, handle incoming messages
            data = await websocket.receive_text()
            
            # Handle subscription changes
            try:
                message = json.loads(data)
                action = message.get("action")
                
                if action == "subscribe":
                    channels_to_sub = message.get("channels", [])
                    websocket_service.manager.subscribe(websocket, channels_to_sub)
                    
                elif action == "unsubscribe":
                    channels_to_unsub = message.get("channels", [])
                    websocket_service.manager.unsubscribe(websocket, channels_to_unsub)
                    
            except json.JSONDecodeError:
                pass
                
    except WebSocketDisconnect:
        websocket_service.manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        websocket_service.manager.disconnect(websocket)
