import asyncio
import json
import logging
import websockets
from threading import Thread
from typing import Callable, Dict, Any

logger = logging.getLogger(__name__)

class KiwoomWSClient:
    def __init__(self, uri: str = "ws://localhost:8000/ws/kiwoom"):
        self.uri = uri
        self.ws = None
        self.loop = asyncio.new_event_loop()
        self.thread = Thread(target=self._start_loop, daemon=True)
        self.handlers: Dict[str, Callable] = {}
        
    def _start_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._connect_and_listen())

    def start(self):
        self.thread.start()

    def register_handler(self, action: str, handler: Callable):
        self.handlers[action] = handler

    async def _connect_and_listen(self):
        while True:
            try:
                logger.info(f"Connecting to Brain Server at {self.uri}...")
                async with websockets.connect(self.uri) as ws:
                    self.ws = ws
                    logger.info("Connected to 64-bit Brain Server.")
                    await self.send_message_async("ping", {"msg": "Hello from 32-bit Execution Node"})
                    
                    async for message in ws:
                        data = json.loads(message)
                        action = data.get("action")
                        payload = data.get("payload", {})
                        handler = self.handlers.get(action)
                        if handler:
                            try:
                                handler(payload)
                            except Exception as e:
                                logger.error(f"Handler error for {action}: {e}", exc_info=True)
                        else:
                            logger.warning(f"No handler for action: {action}")
            except Exception as e:
                logger.error(f"WebSocket disconnected or failed to connect: {e}")
                await asyncio.sleep(3) # Reconnect delay

    async def send_message_async(self, action: str, payload: Dict[str, Any]):
        if self.ws:
            msg = json.dumps({"action": action, "payload": payload})
            await self.ws.send(msg)

    def send_message_sync(self, action: str, payload: Dict[str, Any]):
        if self.ws:
            msg = json.dumps({"action": action, "payload": payload})
            asyncio.run_coroutine_threadsafe(self.ws.send(msg), self.loop)
        else:
            logger.warning(f"WebSocket not ready. Dropped msg: {action}")
