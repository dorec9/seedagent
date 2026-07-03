import os
import logging
import aiohttp
from common.config import BotConfig

logger = logging.getLogger(__name__)

class DiscordNotifier:
    def __init__(self):
        self.config = BotConfig()
        
    async def send_alert(self, message: str):
        url = os.getenv("DISCORD_WEBHOOK_URL") or self.config.discord_webhook_url
        logger.info(f"Using Discord Webhook URL: {url}")
        if not url:
            logger.debug("Discord Webhook URL not set. Skipping alert.")
            return False
            
        payload = {"content": message}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status in (200, 204):
                        logger.info("Discord alert sent successfully.")
                        return True
                    else:
                        logger.error(f"Failed to send Discord alert: {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"Error sending Discord alert: {e}")
            return False

    async def send_trade_alert(self, action: str, code: str, name: str, price: int, qty: int):
        """Format and send a trade alert to Discord."""
        emoji = "🔴" if action == "BUY" or "BUY" in action else "🔵"
        action_text = "매수" if action == "BUY" or "BUY" in action else "매도"
        
        msg = f"{emoji} **[{action_text} 주문 발생]**\n"
        msg += f"- **종목명**: {name} ({code})\n"
        msg += f"- **주문단가**: {price:,} 원\n"
        msg += f"- **주문수량**: {qty:,} 주\n"
        msg += f"- **총 금액**: {price * qty:,} 원"
        
        return await self.send_alert(msg)

notifier = DiscordNotifier()
