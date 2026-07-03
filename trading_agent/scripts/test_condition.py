import asyncio
import json
import websockets

async def test_condition_search():
    uri = "ws://127.0.0.1:8765"
    print(f"Connecting to 32-bit client at {uri}...")
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected! Sending 'load_condition' command...")
            
            # 1. Send load_condition
            load_cmd = {
                "command": "load_condition"
            }
            await websocket.send(json.dumps(load_cmd))
            
            # 2. Wait for response
            while True:
                response = await websocket.recv()
                data = json.loads(response)
                
                event_type = data.get("type")
                if event_type == "condition_loaded":
                    print("\n✅ [SUCCESS] Condition list loaded from Kiwoom Server!")
                    print("Conditions:", json.dumps(data.get("conditions", []), indent=2, ensure_ascii=False))
                    break
                elif event_type == "kiwoom_msg":
                    print(f"Kiwoom MSG: {data.get('msg')}")
                else:
                    print(f"Ignored event: {event_type}")
                    
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        print("Please make sure the 32-bit client (python client/main.py) is running first!")

if __name__ == "__main__":
    asyncio.run(test_condition_search())
