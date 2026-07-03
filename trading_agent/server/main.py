import os
import sys
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
from dotenv import load_dotenv

load_dotenv()

# Ensure the parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from common.config import load_config
from server.ws_manager import manager
from server.storage import QuoteSnapshotStore

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from server.bot import ServerTradingBot

app = FastAPI(title="SeedAgent 64-bit Server")

# Initialize DB
config = load_config()
db_url = config.database_url or "postgresql://postgres:password@localhost:5432/trading"
store = QuoteSnapshotStore(db_url)
store.initialize()
store.initialize_trade_logs()

bot = ServerTradingBot(store)

@app.on_event("startup")
async def startup_event():
    logger.info("FastAPI Server Started. Ready for Kiwoom 32-bit Client.")
    bot.start()

@app.websocket("/ws/kiwoom")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            await manager.process_message(data, websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket Error: {e}", exc_info=True)
        manager.disconnect(websocket)

# Basic message handlers
async def handle_tr_data(payload: dict, websocket: WebSocket):
    trcode = payload.get("trcode")
    rqname = payload.get("rqname")
    logger.info(f"Received TR Data: {trcode} ({rqname})")
    await bot.on_tr_data_received(payload, websocket)

manager.register_handler("tr_data", handle_tr_data)

async def handle_ping(payload: dict, websocket: WebSocket):
    logger.info(f"Ping received from client. Payload: {payload}")
    await manager.send_command("pong", {"status": "ok"})

manager.register_handler("ping", handle_ping)

if __name__ == "__main__":
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)
