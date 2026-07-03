"""Entry point for the Kiwoom OCX Trading Bot."""

from __future__ import annotations

import sys
import logging

from PyQt5.QtWidgets import QApplication

from .config import load_config
from .storage import QuoteSnapshotStore
from .bot import TradingBot, TradingBotConfig

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main() -> None:
    config = load_config()
    
    db_url = config.database_url
    if not db_url:
        logger.warning("DATABASE_URL is not set in .env. Using mock in-memory or failing storage.")
        db_url = "postgresql://postgres:password@localhost:5432/trading"
        
    store = QuoteSnapshotStore(db_url)
    
    app = QApplication(sys.argv)
    
    bot_config = TradingBotConfig(
        target_stocks=["005930", "000660", "035420"], # 테스트용 타겟 종목: 삼성전자, SK하이닉스, 네이버
        loop_interval_seconds=10.0
    )
    
    bot = TradingBot(config=config, bot_config=bot_config, access_token="", store=store)
    bot.run()
    
    logger.info("Starting PyQt Event Loop. Awaiting Kiwoom login popup...")
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
