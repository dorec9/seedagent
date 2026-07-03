import os
import sys
import logging
import time

# Ensure the parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PyQt5.QtWidgets import QApplication
from client.kiwoom_api import KiwoomAPI
from client.ws_client import KiwoomWSClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ExecutionNode:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.api = KiwoomAPI()
        self.ws = KiwoomWSClient()

        # Connect WS commands to Kiwoom Actions
        self.ws.register_handler("comm_rq_data", self.handle_comm_rq_data)
        self.ws.register_handler("send_order", self.handle_send_order)
        self.ws.register_handler("set_real_reg", self.handle_set_real_reg)
        self.ws.register_handler("load_condition", self.handle_load_condition)
        self.ws.register_handler("send_condition", self.handle_send_condition)

        # Hook Kiwoom Events
        self.api.on_tr_data_callback = self.on_tr_data
        self.api.on_real_data_callback = self.on_real_data
        self.api.on_chejan_data_callback = self.on_chejan_data
        self.api.on_msg_callback = self.on_msg
        self.api.on_login_callback = self.on_connect
        
        # Condition callbacks
        self.api.on_receive_condition_ver_callback = self.on_receive_condition_ver
        self.api.on_receive_tr_condition_callback = self.on_receive_tr_condition
        self.api.on_receive_real_condition_callback = self.on_receive_real_condition

    def on_connect(self, err_code):
        if err_code == 0:
            logger.info("Successfully logged into Kiwoom.")
            account_no = self.api.get_login_info("ACCNO").split(';')[0]
            self.ws.send_message_sync("login_success", {"account_no": account_no})
        else:
            logger.error(f"Login failed with err code: {err_code}")

    def handle_comm_rq_data(self, payload):
        rqname = payload.get("rqname")
        trcode = payload.get("trcode")
        next_val = payload.get("next", 0)
        screen_no = payload.get("screen_no", "1000")
        inputs = payload.get("inputs", {})
        
        logger.info(f"WS Command -> comm_rq_data: {trcode} ({rqname})")
        for k, v in inputs.items():
            self.api.set_input_value(k, str(v))
        self.api.comm_rq_data(rqname, trcode, next_val, screen_no)

    def handle_send_order(self, payload):
        rqname = payload.get("rqname")
        screen_no = payload.get("screen_no", "1001")
        acc_no = payload.get("acc_no")
        order_type = payload.get("order_type")
        code = payload.get("code")
        qty = payload.get("qty")
        price = payload.get("price")
        hoga_gb = payload.get("hoga_gb", "03")
        org_order_no = payload.get("org_order_no", "")
        
        logger.info(f"WS Command -> send_order: {rqname} {code}")
        res = self.api.send_order(rqname, screen_no, acc_no, order_type, code, qty, price, hoga_gb, org_order_no)
        logger.info(f"SendOrder result: {res}")

    def handle_set_real_reg(self, payload):
        screen_no = payload.get("screen_no", "2000")
        code_list = payload.get("code_list")
        fid_list = payload.get("fid_list")
        opt_type = payload.get("opt_type", "1")
        self.api.set_real_reg(screen_no, code_list, fid_list, opt_type)

    def on_tr_data(self, trcode, rqname, parsed_data):
        logger.info(f"Received TR Data: {rqname}")
        self.ws.send_message_sync("tr_data", {
            "trcode": trcode,
            "rqname": rqname,
            "data": parsed_data
        })

    def on_real_data(self, code, real_type, real_data):
        pass

    def on_chejan_data(self, gubun, item_cnt, fid_list):
        logger.info(f"Chejan Data received: gubun={gubun}")
        if gubun == "0":  # 주문체결
            order_no = self.api.get_chejan_data(9203).strip()
            code = self.api.get_chejan_data(9001).strip().replace("A", "")
            name = self.api.get_chejan_data(302).strip()
            order_type = self.api.get_chejan_data(905).strip() # "+매수", "-매도"
            exec_price = self.api.get_chejan_data(910).strip()
            exec_qty = self.api.get_chejan_data(911).strip()
            
            if exec_qty and int(exec_qty) > 0:
                payload = {
                    "order_no": order_no,
                    "code": code,
                    "name": name,
                    "action": "BUY" if "매수" in order_type else "SELL",
                    "price": int(exec_price),
                    "qty": int(exec_qty)
                }
                logger.info(f"Trade Executed: {payload}")
                self.ws.send_message_sync("chejan_data", payload)

    def on_msg(self, rqname, trcode, msg):
        self.ws.send_message_sync("kiwoom_msg", {
            "rqname": rqname, "trcode": trcode, "msg": msg
        })

    # --- Condition Search Handlers ---
    def handle_load_condition(self, payload):
        logger.info("WS Command -> load_condition")
        res = self.api.get_condition_load()
        if res != 1:
            logger.error("Failed to load conditions.")

    def handle_send_condition(self, payload):
        scr_no = payload.get("screen_no", "3000")
        cond_name = payload.get("cond_name")
        cond_index = payload.get("cond_index")
        search_type = payload.get("search_type", 1)  # Default 1: Real-time
        logger.info(f"WS Command -> send_condition: {cond_name}")
        self.api.send_condition(scr_no, cond_name, cond_index, search_type)

    def on_receive_condition_ver(self, ret, msg):
        cond_list_str = self.api.get_condition_name_list()
        # Parse "0^조건명1;1^조건명2;"
        conditions = []
        if cond_list_str:
            for item in cond_list_str.split(';'):
                if '^' in item:
                    idx, name = item.split('^')
                    conditions.append({"index": int(idx), "name": name})
        self.ws.send_message_sync("condition_loaded", {"conditions": conditions, "msg": msg})

    def on_receive_tr_condition(self, scr_no, code_list, cond_name, cond_index, next_val):
        codes = [c for c in code_list.split(';') if c]
        self.ws.send_message_sync("tr_condition", {
            "cond_name": cond_name,
            "codes": codes
        })

    def on_receive_real_condition(self, code, event_type, cond_name, cond_index):
        # event_type: "I" = Insert, "D" = Delete
        self.ws.send_message_sync("real_condition", {
            "code": code,
            "event_type": event_type,
            "cond_name": cond_name
        })

    def run(self):
        self.ws.start()
        time.sleep(1)
        
        logger.info("Starting Kiwoom OCX Execution Node...")
        if self.api.login():
            sys.exit(self.app.exec_())
        else:
            logger.error("Failed to connect to Kiwoom.")

if __name__ == "__main__":
    node = ExecutionNode()
    node.run()
