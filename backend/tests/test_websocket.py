"""
WebSocket Service Tests for ATS-SMT PRO
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class TestConnectionManager:
    """Test WebSocket connection management"""
    
    def test_initialization(self):
        """Test ConnectionManager initialization"""
        from backend.services.websocket_service import ConnectionManager
        
        manager = ConnectionManager()
        assert len(manager.active_connections) == 0
        assert len(manager.subscriptions) == 0
        
    @pytest.mark.asyncio
    async def test_connect(self):
        """Test WebSocket connection"""
        from backend.services.websocket_service import ConnectionManager
        
        manager = ConnectionManager()
        mock_websocket = AsyncMock()
        
        await manager.connect(mock_websocket, ["prices", "signals"])
        
        assert len(manager.active_connections) == 1
        assert mock_websocket in manager.active_connections
        assert "prices" in manager.subscriptions[mock_websocket]
        assert "signals" in manager.subscriptions[mock_websocket]
        
    def test_disconnect(self):
        """Test WebSocket disconnection"""
        from backend.services.websocket_service import ConnectionManager
        
        manager = ConnectionManager()
        mock_websocket = MagicMock()
        
        manager.active_connections.add(mock_websocket)
        manager.subscriptions[mock_websocket] = {"prices"}
        
        manager.disconnect(mock_websocket)
        
        assert len(manager.active_connections) == 0
        assert mock_websocket not in manager.subscriptions
        
    def test_subscribe(self):
        """Test channel subscription"""
        from backend.services.websocket_service import ConnectionManager
        
        manager = ConnectionManager()
        mock_websocket = MagicMock()
        
        manager.subscriptions[mock_websocket] = {"prices"}
        manager.subscribe(mock_websocket, ["orders", "positions"])
        
        assert "prices" in manager.subscriptions[mock_websocket]
        assert "orders" in manager.subscriptions[mock_websocket]
        assert "positions" in manager.subscriptions[mock_websocket]
        
    def test_unsubscribe(self):
        """Test channel unsubscription"""
        from backend.services.websocket_service import ConnectionManager
        
        manager = ConnectionManager()
        mock_websocket = MagicMock()
        
        manager.subscriptions[mock_websocket] = {"prices", "orders", "signals"}
        manager.unsubscribe(mock_websocket, ["orders"])
        
        assert "prices" in manager.subscriptions[mock_websocket]
        assert "orders" not in manager.subscriptions[mock_websocket]
        assert "signals" in manager.subscriptions[mock_websocket]
        
    def test_is_subscribed_wildcard(self):
        """Test wildcard subscription"""
        from backend.services.websocket_service import ConnectionManager
        
        manager = ConnectionManager()
        mock_websocket = MagicMock()
        
        manager.subscriptions[mock_websocket] = {"*"}
        
        assert manager._is_subscribed(mock_websocket, "prices") is True
        assert manager._is_subscribed(mock_websocket, "any_channel") is True
        
    def test_is_subscribed_specific(self):
        """Test specific channel subscription"""
        from backend.services.websocket_service import ConnectionManager
        
        manager = ConnectionManager()
        mock_websocket = MagicMock()
        
        manager.subscriptions[mock_websocket] = {"prices", "orders"}
        
        assert manager._is_subscribed(mock_websocket, "prices") is True
        assert manager._is_subscribed(mock_websocket, "orders") is True
        assert manager._is_subscribed(mock_websocket, "signals") is False
        
    def test_is_subscribed_not_connected(self):
        """Test subscription check for non-connected websocket"""
        from backend.services.websocket_service import ConnectionManager
        
        manager = ConnectionManager()
        mock_websocket = MagicMock()
        
        assert manager._is_subscribed(mock_websocket, "prices") is False


class TestWebSocketService:
    """Test main WebSocket service"""
    
    def test_service_initialization(self):
        """Test WebSocketService initialization"""
        from backend.services.websocket_service import WebSocketService
        
        service = WebSocketService()
        assert service._running is False
        assert len(service._tasks) == 0
        assert service.manager is not None
        
    @pytest.mark.asyncio
    async def test_service_start(self):
        """Test service start"""
        from backend.services.websocket_service import WebSocketService
        
        service = WebSocketService()
        await service.start()
        
        assert service._running is True
        
    @pytest.mark.asyncio
    async def test_service_stop(self):
        """Test service stop"""
        from backend.services.websocket_service import WebSocketService
        
        service = WebSocketService()
        await service.start()
        await service.stop()
        
        assert service._running is False
        assert len(service._tasks) == 0


class TestBroadcastFunctions:
    """Test broadcast functionality"""
    
    @pytest.mark.asyncio
    async def test_broadcast_price(self):
        """Test price broadcast"""
        from backend.services.websocket_service import WebSocketService
        
        service = WebSocketService()
        service.manager.broadcast = AsyncMock()
        
        await service.broadcast_price("BTC/USDT", 50000.0, 2.5)
        
        service.manager.broadcast.assert_called_once_with(
            "prices",
            {
                "symbol": "BTC/USDT",
                "price": 50000.0,
                "change_24h": 2.5
            }
        )
        
    @pytest.mark.asyncio
    async def test_broadcast_position(self):
        """Test position broadcast"""
        from backend.services.websocket_service import WebSocketService
        
        service = WebSocketService()
        service.manager.broadcast = AsyncMock()
        
        position_data = {
            "symbol": "ETH/USDT",
            "side": "LONG",
            "entry": 3000.0,
            "pnl": 150.0
        }
        
        await service.broadcast_position(position_data)
        
        service.manager.broadcast.assert_called_once_with(
            "positions",
            position_data
        )
        
    @pytest.mark.asyncio
    async def test_broadcast_signal(self):
        """Test signal broadcast"""
        from backend.services.websocket_service import WebSocketService
        
        service = WebSocketService()
        service.manager.broadcast = AsyncMock()
        
        signal_data = {
            "symbol": "SOL/USDT",
            "action": "LONG",
            "entry": 100.0,
            "votes": "3/3"
        }
        
        await service.broadcast_signal(signal_data)
        
        service.manager.broadcast.assert_called_once_with(
            "signals",
            signal_data
        )
        
    @pytest.mark.asyncio
    async def test_broadcast_order(self):
        """Test order broadcast"""
        from backend.services.websocket_service import WebSocketService
        
        service = WebSocketService()
        service.manager.broadcast = AsyncMock()
        
        order_data = {
            "order_id": "123",
            "symbol": "BTC/USDT",
            "status": "FILLED"
        }
        
        await service.broadcast_order(order_data)
        
        service.manager.broadcast.assert_called_once_with(
            "orders",
            order_data
        )
        
    @pytest.mark.asyncio
    async def test_broadcast_risk(self):
        """Test risk broadcast"""
        from backend.services.websocket_service import WebSocketService
        
        service = WebSocketService()
        service.manager.broadcast = AsyncMock()
        
        risk_data = {
            "daily_pnl": 500.0,
            "drawdown": -2.5,
            "exposure": 0.15
        }
        
        await service.broadcast_risk(risk_data)
        
        service.manager.broadcast.assert_called_once_with(
            "risk",
            risk_data
        )
        
    @pytest.mark.asyncio
    async def test_broadcast_engine_status(self):
        """Test engine status broadcast"""
        from backend.services.websocket_service import WebSocketService
        
        service = WebSocketService()
        service.manager.broadcast = AsyncMock()
        
        status_data = {
            "running": True,
            "mode": "PAPER",
            "emergency_stop": False
        }
        
        await service.broadcast_engine_status(status_data)
        
        service.manager.broadcast.assert_called_once_with(
            "engine",
            status_data
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
