import os
import yfinance as yf
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TARGET_STOCKS = {
    "005930": "005930.KS", # 삼성전자
    "000660": "000660.KS", # SK하이닉스
    "035420": "035420.KS"  # 네이버
}

def add_features(group):
    group = group.sort_values('date') if 'date' in group.columns else group.sort_index()
    
    # 이동평균선
    group['MA5'] = group['close'].rolling(window=5).mean()
    group['MA20'] = group['close'].rolling(window=20).mean()
    
    # RSI 14
    delta = group['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    group['RSI14'] = 100 - (100 / (1 + rs))
    
    # Label (Target): 5일 뒤 종가가 2% 이상 상승했는가? (1=상승, 0=하락/횡보)
    group['Future_Close'] = group['close'].shift(-5)
    group['Target_Yield'] = (group['Future_Close'] - group['close']) / group['close'] * 100
    group['Label'] = (group['Target_Yield'] >= 2.0).astype(int)
    
    return group.dropna()

def main():
    logger.info("Downloading historical data from Yahoo Finance (past 5 years)...")
    all_data = []
    
    for code, ticker in TARGET_STOCKS.items():
        logger.info(f"Fetching {ticker} ({code})...")
        df = yf.download(ticker, period="5y", progress=False)
        if df.empty:
            logger.warning(f"No data for {ticker}")
            continue
            
        df = df.reset_index()
        # yfinance columns might be MultiIndex in recent versions, let's handle flat columns
        df.columns = [str(c[0] if isinstance(c, tuple) else c).lower() for c in df.columns]
        df['stock_code'] = code
        all_data.append(df)
        
    if not all_data:
        logger.error("Failed to download any data.")
        return
        
    combined_df = pd.concat(all_data, ignore_index=True)
    
    logger.info("Computing technical indicators (MA5, MA20, RSI14)...")
    df_features = combined_df.groupby('stock_code', group_keys=False).apply(add_features)
    
    X = df_features[['close', 'MA5', 'MA20', 'RSI14']].rename(columns={'close': 'close_price'})
    y = df_features['Label']
    
    logger.info(f"Total training samples: {len(X)}")
    
    logger.info("Training RandomForestClassifier...")
    # Time-series split (no shuffling to prevent data leakage)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
    
    model = RandomForestClassifier(n_estimators=100, max_depth=5, class_weight='balanced', random_state=42)
    model.fit(X_train, y_train)
    
    logger.info("Model Evaluation on Test Set:")
    print(classification_report(y_test, model.predict(X_test)))
    
    # Ensure artifacts directory exists
    os.makedirs("artifacts", exist_ok=True)
    
    save_path = "artifacts/baseline_model.joblib"
    joblib.dump(model, save_path)
    logger.info(f"SUCCESS: Model saved to {save_path}! 64-bit Server will now use this for real ML predictions.")

if __name__ == "__main__":
    main()
