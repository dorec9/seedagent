"""Kiwoom OpenAPI+ PyQt5 Wrapper."""

import logging
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop, pyqtSignal

logger = logging.getLogger(__name__)

class KiwoomAPI(QAxWidget):
    """PyQt5 wrapper for Kiwoom OpenAPI+ OCX Component."""
    
    # Use callbacks for UI decoupling instead of pyqtSignal
    on_login_callback = None
    on_real_data_callback = None
    on_chejan_data_callback = None
    on_tr_data_callback = None
    on_msg_callback = None
    
    def __init__(self):
        super().__init__()
        # Load the Kiwoom OCX component
        self.setControl("KHOPENAPI.KHOpenAPICtrl.1")
        
        self.login_loop = None
        self._setup_events()
        
    def _setup_events(self):
        """Connect OCX events to Python slots."""
        self.OnEventConnect.connect(self._on_event_connect)
        self.OnReceiveRealData.connect(self._on_receive_real_data)
        self.OnReceiveChejanData.connect(self._on_receive_chejan_data)
        self.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.OnReceiveMsg.connect(self._on_receive_msg)
        
    def login(self) -> bool:
        """Trigger login UI and wait for connection."""
        err_code = self.dynamicCall("CommConnect()")
        if err_code != 0:
            logger.error("Failed to trigger CommConnect.")
            return False
            
        self.login_loop = QEventLoop()
        self.login_loop.exec_()
        return True

    def get_login_info(self, tag: str) -> str:
        """Get login info like ACCOUNT_CNT or ACCNO."""
        return self.dynamicCall("GetLoginInfo(QString)", tag)
        
    def set_real_reg(self, screen_no: str, code_list: str, fid_list: str, opt_type: str) -> None:
        """Register for real-time data."""
        self.dynamicCall("SetRealReg(QString, QString, QString, QString)", screen_no, code_list, fid_list, opt_type)
        
    def set_real_remove(self, screen_no: str, del_code: str) -> None:
        """Remove real-time registration."""
        self.dynamicCall("SetRealRemove(QString, QString)", screen_no, del_code)

    def set_input_value(self, id_str: str, value: str) -> None:
        """Set TR input value."""
        self.dynamicCall("SetInputValue(QString, QString)", id_str, value)

    def comm_rq_data(self, rqname: str, trcode: str, prev_next: int, screen_no: str) -> int:
        """Request TR data."""
        return self.dynamicCall("CommRqData(QString, QString, int, QString)", rqname, trcode, prev_next, screen_no)

    def get_comm_data(self, trcode: str, rqname: str, index: int, item_name: str) -> str:
        """Get received TR data."""
        return self.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, index, item_name).strip()

    def get_repeat_cnt(self, trcode: str, rqname: str) -> int:
        """Get the number of repeated data in TR."""
        return self.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)

    def send_order(self, rqname: str, screen_no: str, acc_no: str, order_type: int, code: str, qty: int, price: int, hoga_gb: str, org_order_no: str) -> int:
        """Send an order."""
        res = self.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            [rqname, screen_no, acc_no, order_type, code, qty, price, hoga_gb, org_order_no]
        )
        return res

    def get_comm_real_data(self, code: str, fid: int) -> str:
        """Get real data by FID."""
        return self.dynamicCall("GetCommRealData(QString, int)", code, fid)
        
    def get_chejan_data(self, fid: int) -> str:
        """Get Chejan data by FID."""
        return self.dynamicCall("GetChejanData(int)", fid)

    # --- Condition Search ---
    def get_condition_load(self) -> int:
        """Load conditions from server."""
        return self.dynamicCall("GetConditionLoad()")
        
    def get_condition_name_list(self) -> str:
        """Get list of loaded conditions. Format: index^name;index^name;"""
        return self.dynamicCall("GetConditionNameList()")
        
    def send_condition(self, scr_no: str, cond_name: str, cond_index: int, search_type: int) -> int:
        """
        Request condition search.
        search_type: 0 = normal (one-time), 1 = real-time
        """
        return self.dynamicCall("SendCondition(QString, QString, int, int)", scr_no, cond_name, cond_index, search_type)
        
    def send_condition_stop(self, scr_no: str, cond_name: str, cond_index: int) -> None:
        """Stop real-time condition search."""
        self.dynamicCall("SendConditionStop(QString, QString, int)", scr_no, cond_name, cond_index)

    # --- Event Handlers ---
    on_receive_condition_ver_callback = None
    on_receive_tr_condition_callback = None
    on_receive_real_condition_callback = None

    def _setup_events(self):
        """Connect OCX events to Python slots."""
        self.OnEventConnect.connect(self._on_event_connect)
        self.OnReceiveRealData.connect(self._on_receive_real_data)
        self.OnReceiveChejanData.connect(self._on_receive_chejan_data)
        self.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.OnReceiveMsg.connect(self._on_receive_msg)
        
        # Condition events
        self.OnReceiveConditionVer.connect(self._on_receive_condition_ver)
        self.OnReceiveTrCondition.connect(self._on_receive_tr_condition)
        self.OnReceiveRealCondition.connect(self._on_receive_real_condition)

    def _on_receive_condition_ver(self, ret: int, msg: str):
        logger.info(f"OnReceiveConditionVer: ret={ret}, msg={msg}")
        if self.on_receive_condition_ver_callback:
            self.on_receive_condition_ver_callback(ret, msg)
            
    def _on_receive_tr_condition(self, scr_no: str, code_list: str, cond_name: str, cond_index: int, next_val: int):
        logger.info(f"OnReceiveTrCondition: {cond_name}")
        if self.on_receive_tr_condition_callback:
            self.on_receive_tr_condition_callback(scr_no, code_list, cond_name, cond_index, next_val)
            
    def _on_receive_real_condition(self, code: str, event_type: str, cond_name: str, cond_index: str):
        # event_type: "I" (Insert/편입), "D" (Delete/이탈)
        logger.info(f"OnReceiveRealCondition: {code} {event_type} in {cond_name}")
        if self.on_receive_real_condition_callback:
            self.on_receive_real_condition_callback(code, event_type, cond_name, cond_index)
    def _on_event_connect(self, err_code: int):
        logger.info(f"OnEventConnect triggered. Err code: {err_code}")
        if self.login_loop:
            self.login_loop.exit()
        if self.on_login_callback:
            self.on_login_callback(err_code)

    def _on_receive_real_data(self, code: str, real_type: str, real_data: str):
        if self.on_real_data_callback:
            self.on_real_data_callback(code, real_type, real_data)
        
    def _on_receive_chejan_data(self, gubun: str, item_cnt: int, fid_list: str):
        if self.on_chejan_data_callback:
            self.on_chejan_data_callback(gubun, item_cnt, fid_list)
        
    def _on_receive_tr_data(self, scr_no, rqname, trcode, record_name, prev_next, data_len, err_code, msg1, msg2):
        logger.info(f"OnReceiveTrData: {rqname}, {trcode}")
        
        parsed_data = None
        
        if trcode == "opt10081":
            # 주식일봉차트조회요청
            cnt = self.get_repeat_cnt(trcode, rqname)
            ohlcv_list = []
            for i in range(cnt):
                date = self.get_comm_data(trcode, rqname, i, "일자")
                open_p = abs(int(self.get_comm_data(trcode, rqname, i, "시가") or 0))
                high_p = abs(int(self.get_comm_data(trcode, rqname, i, "고가") or 0))
                low_p = abs(int(self.get_comm_data(trcode, rqname, i, "저가") or 0))
                close_p = abs(int(self.get_comm_data(trcode, rqname, i, "현재가") or 0))
                vol = int(self.get_comm_data(trcode, rqname, i, "거래량") or 0)
                
                ohlcv_list.append({
                    "date": date,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                    "volume": vol
                })
            parsed_data = ohlcv_list
            
        elif trcode == "opw00018":
            try:
                def safe_int(s):
                    if not s: return 0
                    try: return int(s.replace(",", "").strip())
                    except: return 0
                
                def safe_float(s):
                    if not s: return 0.0
                    try: return float(s.replace(",", "").strip())
                    except: return 0.0

                total_buy = safe_int(self.get_comm_data(trcode, rqname, 0, "총매입금액"))
                total_eval = safe_int(self.get_comm_data(trcode, rqname, 0, "총평가금액"))
                total_profit = safe_int(self.get_comm_data(trcode, rqname, 0, "총평가손익금액"))
                profit_rate = safe_float(self.get_comm_data(trcode, rqname, 0, "총수익률(%)"))
                est_asset = safe_int(self.get_comm_data(trcode, rqname, 0, "추정예탁자산"))
                
                cnt = self.get_repeat_cnt(trcode, rqname)
                positions = []
                for i in range(cnt):
                    code_raw = self.get_comm_data(trcode, rqname, i, "종목번호")
                    code = code_raw.replace("A", "").strip() if code_raw else ""
                    name = self.get_comm_data(trcode, rqname, i, "종목명")
                    qty = safe_int(self.get_comm_data(trcode, rqname, i, "보유수량"))
                    buy_price = safe_int(self.get_comm_data(trcode, rqname, i, "매입가"))
                    current_price = safe_int(self.get_comm_data(trcode, rqname, i, "현재가"))
                    eval_amount = safe_int(self.get_comm_data(trcode, rqname, i, "평가금액"))
                    profit_amount = safe_int(self.get_comm_data(trcode, rqname, i, "평가손익"))
                    profit_rt = safe_float(self.get_comm_data(trcode, rqname, i, "수익률(%)"))
                    
                    if code:
                        positions.append({
                            "code": code,
                            "name": name,
                            "qty": qty,
                            "buy_price": buy_price,
                            "current_price": current_price,
                            "eval_amount": eval_amount,
                            "profit_amount": profit_amount,
                            "profit_rate": profit_rt
                        })
                    
                parsed_data = {
                    "summary": {
                        "total_buy": total_buy,
                        "total_eval": total_eval,
                        "total_profit": total_profit,
                        "profit_rate": profit_rate,
                        "est_asset": est_asset
                    },
                    "positions": positions
                }
            except Exception as e:
                import traceback
                logger.error(f"Error parsing opw00018: {e}\n{traceback.format_exc()}")
        if self.on_tr_data_callback:
            self.on_tr_data_callback(trcode, rqname, parsed_data)
        
    def _on_receive_msg(self, scr_no, rqname, trcode, msg):
        logger.info(f"OnReceiveMsg: {rqname} -> {msg}")
        if self.on_msg_callback:
            self.on_msg_callback(rqname, trcode, msg)
