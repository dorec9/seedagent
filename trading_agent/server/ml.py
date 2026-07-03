import logging
import math
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class MLPredictor:
    """
    Machine Learning Predictor interface.
    Falls back to a Rule-based (RSI & MA) strategy when no actual model (.joblib) is provided.
    """
    def __init__(self, model_path: str = None):
        self.model_path = model_path
        self.model = None
        
        if self.model_path:
            self._load_model()
            
    def _load_model(self):
        try:
            import joblib
            self.model = joblib.load(self.model_path)
            logger.info(f"Successfully loaded ML model from {self.model_path}")
        except Exception as e:
            logger.warning(f"Could not load ML model from {self.model_path}. Falling back to Rule-based mock model. Error: {e}")
            self.model = None

    def predict_proba(self, ohlcv_data: List[Dict[str, Any]]) -> float:
        """
        Takes a list of OHLCV daily dictionaries, oldest to newest (or newest to oldest)
        and returns a buy probability [0.0, 1.0].
        
        Args:
            ohlcv_data: [{"date": "...", "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}, ...]
            NOTE: Kiwoom usually returns newest first.
        """
        if not ohlcv_data or len(ohlcv_data) < 14:
            return 0.0
            
        if self.model:
            try:
                import pandas as pd
                # Kiwoom opt10081 usually returns newest first. Reverse to oldest first for rolling calculations.
                df = pd.DataFrame(ohlcv_data)
                
                # Normalize column names to lowercase just in case
                df.columns = [str(c).lower() for c in df.columns]
                
                # Convert 'close' to numeric
                df['close'] = pd.to_numeric(df['close'], errors='coerce')
                df = df.dropna(subset=['close']).iloc[::-1].reset_index(drop=True) # Oldest first
                
                if len(df) < 20:
                    logger.warning("Not enough data to compute MA20 for ML prediction.")
                    return 0.0
                    
                df['ma5'] = df['close'].rolling(window=5).mean()
                df['ma20'] = df['close'].rolling(window=20).mean()
                
                delta = df['close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                df['rsi14'] = 100 - (100 / (1 + rs))
                
                # Get the most recent row (which is now the last row)
                latest_row = df.iloc[-1]
                
                # Features must match the training script: ['close_price', 'MA5', 'MA20', 'RSI14']
                X = pd.DataFrame([{
                    'close_price': latest_row['close'],
                    'MA5': latest_row['ma5'],
                    'MA20': latest_row['ma20'],
                    'RSI14': latest_row['rsi14']
                }])
                
                # RandomForest predict_proba returns array of shape (n_samples, n_classes)
                # Class 1 is "buy" (index 1)
                proba = self.model.predict_proba(X)[0][1]
                logger.info(f"Actual ML Model Probability: {proba*100:.2f}%")
                return proba
                
            except Exception as e:
                logger.error(f"Error computing live ML features: {e}. Falling back to Rule-based.")
                return self._rule_based_predict(ohlcv_data)
            
        # Fallback Rule-based Model
        return self._rule_based_predict(ohlcv_data)
        
    def _rule_based_predict(self, ohlcv_data: List[Dict[str, Any]]) -> float:
        """
        Mock ML model using RSI(14) and 5-day Moving Average.
        Assumes data is newest-first (Index 0 is today).
        """
        try:
            # Get closing prices (newest to oldest)
            closes = [float(day.get("close", 0)) for day in ohlcv_data]
            
            if len(closes) < 15:
                return 0.0
                
            # 1. 5-day MA trend (is today's price above 5-day MA?)
            ma5 = sum(closes[:5]) / 5
            current_price = closes[0]
            ma_trend_score = 0.6 if current_price > ma5 else 0.3
            
            # 2. Compute RSI (14 days)
            # Since closes[0] is newest, differences should be calculated chronologically
            # Reversing for chronological order: oldest to newest
            chronological_closes = closes[:15][::-1] 
            
            gains = 0.0
            losses = 0.0
            for i in range(1, len(chronological_closes)):
                change = chronological_closes[i] - chronological_closes[i-1]
                if change > 0:
                    gains += change
                else:
                    losses += abs(change)
                    
            avg_gain = gains / 14
            avg_loss = losses / 14
            
            if avg_loss == 0:
                rsi = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100.0 - (100.0 / (1.0 + rs))
                
            # Oversold (RSI < 30) gives high probability, Overbought (RSI > 70) gives low probability
            if rsi < 30:
                rsi_score = 0.9
            elif rsi < 50:
                rsi_score = 0.6
            elif rsi < 70:
                rsi_score = 0.4
            else:
                rsi_score = 0.1
                
            # Combine scores
            final_prob = (ma_trend_score * 0.4) + (rsi_score * 0.6)
            
            logger.debug(f"Rule Predict - Price: {current_price}, MA5: {ma5}, RSI: {rsi:.1f} -> Prob: {final_prob:.2f}")
            return final_prob
            
        except Exception as e:
            logger.error(f"Error computing rule-based probability: {e}")
            return 0.0
