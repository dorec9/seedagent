import logging
from typing import List, Callable, Dict, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.handlers: Dict[str, Callable] = {}

    def register_handler(self, action: str, handler: Callable):
        self.handlers[action] = handler

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("New WebSocket client connected.")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info("WebSocket client disconnected.")

    async def send_command(self, action: str, payload: Dict[str, Any]):
        message = {"action": action, "payload": payload}
        for connection in self.active_connections:
            await connection.send_json(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)
            
    async def process_message(self, message: Dict[str, Any], websocket: WebSocket):
        action = message.get("action")
        payload = message.get("payload", {})
        
        handler = self.handlers.get(action)
        if handler:
            try:
                if getattr(handler, "is_coroutine", False) or __import__("inspect").iscoroutinefunction(handler):
                    await handler(payload, websocket)
                else:
                    handler(payload, websocket)
            except Exception as e:
                logger.error(f"Error handling action '{action}': {e}", exc_info=True)
        else:
            logger.warning(f"No handler registered for action: {action}")

manager = ConnectionManager()
