import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

app = FastAPI()

@app.websocket("/ws/kiwoom")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("\n[SUCCESS] 32-bit Client connected to Test Server!", flush=True)
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            action = payload.get("action")
            
            if action == "login_success":
                print(f"[SUCCESS] Login Success! Account: {payload['payload'].get('account_no')}", flush=True)
                print("Sending 'load_condition' command to client...", flush=True)
                await websocket.send_json({"action": "load_condition", "payload": {}})
                
            elif action == "condition_loaded":
                print("\n[SUCCESS] Condition list loaded from Kiwoom Server!", flush=True)
                print("Conditions:", json.dumps(payload.get("payload", {}).get("conditions", []), indent=2, ensure_ascii=False), flush=True)
                print("\nTest finished successfully. You can stop the servers now.", flush=True)
                
            elif action == "kiwoom_msg":
                print(f"Kiwoom MSG: {payload['payload'].get('msg')}", flush=True)
                
    except WebSocketDisconnect:
        print("Client disconnected.")

if __name__ == "__main__":
    uvicorn.run("test_condition_server:app", host="127.0.0.1", port=8000, reload=False)
