import asyncio
import logging
from typing import Dict, Any
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

from common.config import BotConfig, load_config
from server.signals import CandidateFilter, CandidateFilterConfig
from server.ws_manager import manager
from server.notifier import notifier
from server.ml import MLPredictor

logger = logging.getLogger(__name__)

class ServerTradingBot:
    def __init__(self, store):
        self.store = store
        self.config = BotConfig()
        self.kiwoom_config = load_config()
        self.candidate_filter = CandidateFilter(self.store, CandidateFilterConfig())
        self.predictor = MLPredictor("artifacts/baseline_model.joblib")
        self.config.buy_threshold = 0.3  # Force override for test
        self.running = False
        self.pending_orders = set()
        
        # Pending TR requests (rqname -> asyncio.Future)
        self.pending_tr_requests: Dict[str, asyncio.Future] = {}
        
        # Register handler for TR data from WS
        manager.register_handler("tr_data", self.on_tr_data_received)
        manager.register_handler("chejan_data", self.on_chejan_data_received)
        manager.register_handler("login_success", self.on_login_success)
        manager.register_handler("condition_loaded", self.on_condition_loaded)
        manager.register_handler("tr_condition", self.on_tr_condition)
        manager.register_handler("real_condition", self.on_real_condition)

    async def on_login_success(self, payload: dict, websocket):
        logger.info(f"Login success! Requesting load_condition...")
        await manager.send_command("load_condition", {})

    async def on_condition_loaded(self, payload: dict, websocket):
        conditions = payload.get("conditions", [])
        logger.info(f"Condition list loaded: {conditions}")
        target_name = "전종목감시"
        
        target_cond = next((c for c in conditions if c["name"] == target_name), None)
        if target_cond:
            logger.info(f"Found target condition '{target_name}' at index {target_cond['index']}. Starting real-time search...")
            await notifier.send_alert(f"✅ 키움증권 서버에서 '{target_name}' 조건식을 성공적으로 불러왔습니다! 실시간 전 종목 감시를 시작합니다.")
            await manager.send_command("send_condition", {
                "screen_no": "3000",
                "cond_name": target_cond["name"],
                "cond_index": target_cond["index"],
                "search_type": 1  # 1: Real-time
            })
        else:
            logger.error(f"Condition '{target_name}' not found. Please create it in HTS/MTS.")
            await notifier.send_alert(f"⚠️ '{target_name}' 조건식을 찾을 수 없습니다. 영웅문에서 조건식이 서버에 잘 저장되었는지 확인해주세요!\n현재 저장된 조건식: {[c['name'] for c in conditions]}")

    async def _evaluate_and_buy(self, code: str):
        """Fetch OHLCV, predict, and buy if valid."""
        if code in self.pending_orders:
            return
            
        # Avoid rate limits by adding a tiny random jitter
        import random
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        ohlcv = await self.fetch_ohlcv(code)
        if not ohlcv or len(ohlcv) < 60:
            return
            
        features = self.predictor.extract_features(ohlcv)
        if features is None:
            return
            
        proba = self.predictor.predict_proba(features)
        logger.info(f"[{code}] ML Proba: {proba:.4f} (Threshold: {self.config.buy_threshold})")
        
        if proba >= self.config.buy_threshold:
            self.pending_orders.add(code)
            current_price = abs(int(ohlcv[0]["현재가"]))
            qty = max(1, self.config.order_amount // current_price)
            logger.info(f"[{code}] 🎯 BUY SIGNAL! Placing order...")
            await notifier.send_alert(f"🤖 **[AI 포착]** {code}\n확률: {proba:.1%}\n추천 매수수량: {qty}주")
            await self.send_order("BUY", code, qty, current_price, "03") # 03: 시장가
            await asyncio.sleep(5)
            self.pending_orders.discard(code)

    async def on_tr_condition(self, payload: dict, websocket):
        cond_name = payload.get("cond_name")
        codes = payload.get("codes", [])
        logger.info(f"TR Condition [{cond_name}] matching {len(codes)} stocks initially.")
        await notifier.send_alert(f"🔍 최초 검색에서 {len(codes)}개의 종목이 포착되었습니다. AI 분석을 시작합니다...")
        
        # We limit initial processing to avoid API ban (1000/hr max)
        for code in codes[:10]: 
            asyncio.create_task(self._evaluate_and_buy(code))

    async def on_real_condition(self, payload: dict, websocket):
        code = payload.get("code")
        event_type = payload.get("event_type")
        cond_name = payload.get("cond_name")
        
        if event_type == "I":  # Insert
            logger.info(f"Real-time Condition Inserted: {code} in {cond_name}")
            asyncio.create_task(self._evaluate_and_buy(code))

    async def on_chejan_data_received(self, payload: dict, websocket):
        order_no = payload.get("order_no", "")
        code = payload.get("code", "")
        name = payload.get("name", "")
        action = payload.get("action", "")
        price = payload.get("price", 0)
        qty = payload.get("qty", 0)
        yield_pct = payload.get("yield_pct", 0.0)
        
        logger.info(f"Received trade execution: {payload}")
        
        try:
            self.store.save_trade_log(order_no, code, name, action, price, qty, yield_pct)
        except Exception as e:
            logger.error(f"Failed to save trade log: {e}")
            
        amount = price * qty
        msg = f"🚀 **[Trade Executed]**\n" \
              f"**Stock**: {name} ({code})\n" \
              f"**Action**: {action}\n" \
              f"**Price**: {price:,} KRW\n" \
              f"**Quantity**: {qty:,} Shares\n" \
              f"**Total Value**: {amount:,} KRW\n" \
              f"**Order No**: {order_no}"
        
        await notifier.send_alert(msg)

    async def on_tr_data_received(self, payload: dict, websocket):
        rqname = payload.get("rqname")
        if rqname in self.pending_tr_requests:
            future = self.pending_tr_requests.pop(rqname)
            if not future.done():
                future.set_result(payload)

    async def fetch_ohlcv(self, stock_code: str):
        rqname = f"OHLCV_{stock_code}"
        future = asyncio.Future()
        self.pending_tr_requests[rqname] = future
        
        # Rate limit TRs (max 5 per second) -> handled by the loop speed, but good to add sleep
        await asyncio.sleep(0.25)
        
        # Ask 32-bit client to fetch
        await manager.send_command("comm_rq_data", {
            "rqname": rqname,
            "trcode": "opt10081",
            "inputs": {
                "종목코드": stock_code,
                "기준일자": "",
                "수정주가구분": "1"
            }
        })
        
        # Wait for reply with timeout
        try:
            res = await asyncio.wait_for(future, timeout=10.0)
            return res.get("data", [])
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for TR Data: {rqname}")
            self.pending_tr_requests.pop(rqname, None)
            return []

    async def fetch_portfolio(self):
        rqname = "PORTFOLIO_opw00018"
        future = asyncio.Future()
        self.pending_tr_requests[rqname] = future
        
        account_no = self.kiwoom_config.account_no or "7036158231"
        
        await manager.send_command("comm_rq_data", {
            "rqname": rqname,
            "trcode": "opw00018",
            "inputs": {
                "계좌번호": account_no,
                "비밀번호": "",
                "비밀번호입력매체구분": "00",
                "조회구분": "2"
            }
        })
        
        try:
            res = await asyncio.wait_for(future, timeout=10.0)
            return res
        except asyncio.TimeoutError:
            logger.error("Timeout fetching portfolio (opw00018)")
            self.pending_tr_requests.pop(rqname, None)
            return None

    def is_market_open(self) -> bool:
        """Check if the Korean stock market is currently open (09:00 - 15:30 KST, Mon-Fri)."""
        now = datetime.now(KST)
        if now.weekday() >= 5: # Saturday or Sunday
            return False
        market_start = time(9, 0)
        market_end = time(15, 30)
        return market_start <= now.time() <= market_end

    async def evaluation_loop(self):
        self.running = True
        logger.info("64-bit ML Evaluation loop started.")
        while self.running:
            try:
                if not self.is_market_open():
                    logger.info("Market is closed. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)
                    continue
                    
                await asyncio.sleep(self.config.evaluation_interval)
                
                if not manager.active_connections:
                    logger.debug("No 32-bit client connected. Skipping evaluation.")
                    continue
                    
                # 1. Fetch Portfolio
                portfolio_res = await self.fetch_portfolio()
                if not portfolio_res:
                    continue
                    
                # The portfolio_res contains: {"trcode": "opw00018", "rqname": ..., "data": {"summary": {...}, "positions": [...]}}
                portfolio_data = portfolio_res.get("data", {})
                
                account_balance = portfolio_data.get("summary", {})
                cash = account_balance.get("est_asset", 0)  # Use est_asset for cash equivalents
                held_stocks = portfolio_data.get("positions", [])
                
                # 2. Sell Logic (Take Profit / Stop Loss)
                held_codes = set()
                for stock in held_stocks:
                    code = stock.get("code")
                    yield_pct = stock.get("profit_rate", 0.0)
                    qty = stock.get("qty", 0)
                    
                    if qty <= 0:
                        continue
                        
                    held_codes.add(code)
                    
                    if yield_pct >= self.config.take_profit_pct or yield_pct <= self.config.stop_loss_pct:
                        reason = "TAKE_PROFIT" if yield_pct > 0 else "STOP_LOSS"
                        logger.info(f"Selling {code} ({qty} shares) due to {reason} at {yield_pct}%")
                        await manager.send_command("send_order", {
                            "rqname": f"Sell_{code}_{reason}",
                            "acc_no": self.kiwoom_config.account_no or "7036158231",
                            "order_type": 2, # 2 = Market Sell
                            "code": code,
                            "qty": qty,
                            "price": 0,
                            "hoga_gb": "03"
                        })
                        
                # 3. Buy Logic & Position Sizing
                investable_cash = int(cash * self.config.position_size_pct)
                logger.info(f"Total Cash: {cash:,} KRW, Investable Per Trade: {investable_cash:,} KRW")
                
                # Bypass CandidateFilter since the DB is currently empty.
                # Iterate directly over all target_stocks.
                for stock_code in self.config.target_stocks:
                    if stock_code in held_codes or stock_code in self.pending_orders:
                        continue
                        
                    data = await self.fetch_ohlcv(stock_code)
                    await asyncio.sleep(0.3) # API Rate Limiting (5 requests / 1 sec)
                    
                    if not data:
                        continue
                        
                    # Save to DB for historical data collection
                    try:
                        from server.ohlcv import OhlcvBar
                        bars = []
                        for row in data:
                            dt_str = str(row.get("date", "")).strip()
                            if not dt_str: continue
                            bar_time = datetime.combine(datetime.strptime(dt_str, "%Y%m%d").date(), time.min, tzinfo=timezone.utc)
                            bar = OhlcvBar(
                                stock_code=stock_code,
                                stock_name=stock_code,
                                interval="1d",
                                bar_time=bar_time,
                                open_price=abs(int(row.get("open", 0))),
                                high_price=abs(int(row.get("high", 0))),
                                low_price=abs(int(row.get("low", 0))),
                                close_price=abs(int(row.get("close", 0))),
                                volume=abs(int(row.get("volume", 0))),
                                trading_value=0,
                                raw=row
                            )
                            bars.append(bar)
                        if bars:
                            self.store.save_ohlcv_bars(bars)
                    except Exception as e:
                        logger.error(f"Failed to save DB: {e}")
                        
                    prob = self.predictor.predict_proba(data)
                    logger.info(f"ML probability for {stock_code}: {prob*100:.2f}% (Threshold: {self.config.buy_threshold})")
                    
                    if prob >= self.config.buy_threshold:
                        current_price = data[0].get("close", 10000) if data else 10000
                        qty_to_buy = investable_cash // current_price
                        
                        if qty_to_buy <= 0:
                            logger.warning(f"Not enough cash to buy {stock_code} (price: {current_price}, investable: {investable_cash})")
                            continue
                            
                        logger.info(f"Sending Buy Order for {stock_code} via WS. Qty: {qty_to_buy}")
                        
                        # Send Discord notification for Order Placed
                        asyncio.create_task(notifier.send_trade_alert(
                            action="BUY_ORDER_PLACED",
                            code=stock_code,
                            name=f"Stock_{stock_code}",
                            price=current_price,
                            qty=qty_to_buy
                        ))
                        
                        await manager.send_command("send_order", {
                            "rqname": f"Buy_{stock_code}",
                            "acc_no": self.kiwoom_config.account_no or "7036158231",
                            "order_type": 1,
                            "code": stock_code,
                            "qty": qty_to_buy,
                            "price": 0,
                            "hoga_gb": "03"
                        })
                        self.pending_orders.add(stock_code)
                        
            except Exception as e:
                logger.error(f"Evaluation loop error: {e}", exc_info=True)

    def start(self):
        asyncio.create_task(self.evaluation_loop())
