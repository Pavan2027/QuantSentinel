import pandas as pd
import numpy as np
import yfinance as yf
import optuna
import os
import time
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

print("==========================================================")
print("   QuantSentinel 10-Year Fast Bayesian Optimizer  ")
print("==========================================================")

# 1. Configuration
# Diverse 50-stock matrix spanning Mega, Mid, and Small-Caps for ultimate machine learning variance
SYMBOLS = [
    # Mega-Caps (Stability)
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "BHARTIARTL.NS",
    "SBIN.NS", "INFY.NS", "ITC.NS", "HINDUNILVR.NS", "LT.NS",
    "BAJFINANCE.NS", "HCLTECH.NS", "MARUTI.NS", "SUNPHARMA.NS", "TATAMOTORS.NS",
    
    # Mid-Caps (Growth & Volatility)
    "AUBANK.NS", "IDFCFIRSTB.NS", "SUZLON.NS", "IRFC.NS", "ZOMATO.NS",
    "TRENT.NS", "DIXON.NS", "POLYCAB.NS", "HAL.NS", "BEL.NS",
    "BSE.NS", "CDSL.NS", "ANGELONE.NS", "MCX.NS", "IEX.NS",
    
    # Small-Caps / Niche (High Risk Alpha)
    "KPITTECH.NS", "DATAPATTNS.NS", "MAPMYINDIA.NS", "CEINFO.NS", "MTARTECH.NS",
    "TEJASNET.NS", "OLECTRA.NS", "JWL.NS", "TITAGARH.NS", "RVNL.NS",
    "MAZDOCK.NS", "COCHINSHIP.NS", "GRSE.NS", "BDL.NS", "MIDHANI.NS",
    
    # Cyclicals & Defensive Mix
    "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "CIPLA.NS", "DRREDDY.NS"
]

START_DATE = "2015-01-01"
END_DATE   = "2025-01-01"
INFLATION_RATE = 0.06  # 6% per annum deduction

INITIAL_CAPITAL = 100000.0
TRIALS = 150

# 2. Data Loader
def load_and_prepare_data():
    print(f"\n[1/3] Downloading 10-Year History for {len(SYMBOLS)} stocks...")
    data_dict = {}
    
    raw = yf.download(SYMBOLS, start=START_DATE, end=END_DATE, progress=False)
    
    if isinstance(raw.columns, pd.MultiIndex):
        for sym in SYMBOLS:
            try:
                # Extract individual stock
                df = pd.DataFrame({
                    "Open": raw["Open"][sym],
                    "High": raw["High"][sym],
                    "Low": raw["Low"][sym],
                    "Close": raw["Close"][sym]
                }).dropna()
                
                # Precalculate ATR (Average True Range) - 14 period
                df['H-L'] = df['High'] - df['Low']
                df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
                df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
                df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
                df['ATR'] = df['TR'].rolling(14).mean()
                
                # Precalculate RSI - 14 period
                delta = df['Close'].diff()
                up = delta.clip(lower=0)
                down = -1 * delta.clip(upper=0)
                ema_up = up.ewm(com=13, adjust=False).mean()
                ema_down = down.ewm(com=13, adjust=False).mean()
                rs = ema_up / ema_down
                df['RSI'] = 100 - (100 / (1 + rs))
                
                # Drop NAs to cleanly start
                df = df.dropna()
                data_dict[sym] = df
            except Exception as e:
                pass
                
    return data_dict

data_cache = load_and_prepare_data()

# Create aligned numpy tensors for lightning fast loop vectorization
print("[2/3] Aligning tensors for ultra-fast simulation...")
aligned_dates = None
for df in data_cache.values():
    if aligned_dates is None:
        aligned_dates = df.index
    else:
        aligned_dates = aligned_dates.union(df.index)

# Numpy matrices shape: [Num_Days, Num_Stocks]
closes = np.zeros((len(aligned_dates), len(SYMBOLS)))
highs  = np.zeros((len(aligned_dates), len(SYMBOLS)))
lows   = np.zeros((len(aligned_dates), len(SYMBOLS)))
atrs   = np.zeros((len(aligned_dates), len(SYMBOLS)))
rsis   = np.zeros((len(aligned_dates), len(SYMBOLS)))

for col_idx, sym in enumerate(SYMBOLS):
    if sym in data_cache:
        sub = data_cache[sym].reindex(aligned_dates, method='ffill').fillna(0)
        closes[:, col_idx] = sub["Close"].values
        highs[:, col_idx]  = sub["High"].values
        lows[:, col_idx]   = sub["Low"].values
        atrs[:, col_idx]   = sub["ATR"].values
        rsis[:, col_idx]   = sub["RSI"].values


# 3. The Ultra-Fast Core Engine
def run_fast_backtest(sl_pct, tp_pct, trail_trigger, trail_dist, rsi_entry_thresh, max_positions):
    """
    Highly optimized loop evaluating trade executions 
    at roughly ~0.05 seconds per 10-year iteration.
    """
    cash = INITIAL_CAPITAL
    
    # Active positions tracker
    # Store tuples: (qty, entry_price, highest_price, sl_price, tp_price)
    positions = {}  # key: col_idx
    
    for day_idx in range(len(aligned_dates)):
        day_closes = closes[day_idx]
        day_highs  = highs[day_idx]
        day_lows   = lows[day_idx]
        
        # 1. Evaluate Exits First
        exits_to_process = []
        for sym_idx, pos in positions.items():
            qty, entry_price, highest_price, current_sl, tp_price = pos
            cur_price = day_closes[sym_idx]
            if cur_price <= 0: continue
            
            # Update highest price seen for trailing
            if cur_price > highest_price:
                highest_price = cur_price
                
            # Trailing Stop Math
            activation_price = entry_price * (1 + trail_trigger)
            if cur_price >= activation_price:
                new_trail = highest_price * (1 - trail_dist)
                if new_trail > current_sl:
                    current_sl = new_trail
                    
            # Check triggers using Lows/Highs for accuracy
            hit_sl = day_lows[sym_idx] <= current_sl
            hit_tp = day_highs[sym_idx] >= tp_price
            
            if hit_sl or hit_tp:
                exit_price = current_sl if hit_sl else tp_price
                proceeds = qty * exit_price * 0.999 # Add minor 0.1% slippage
                cash += proceeds
                exits_to_process.append(sym_idx)
            else:
                # Save updated state
                positions[sym_idx] = (qty, entry_price, highest_price, current_sl, tp_price)
                
        for e in exits_to_process:
            del positions[e]
            
        # 2. Evaluate Entries
        if len(positions) < max_positions:
            slots = max_positions - len(positions)
            buy_budget = (cash * 0.95) / max_positions  # Allocate linearly
            
            for sym_idx in range(len(SYMBOLS)):
                if slots <= 0: break
                if sym_idx in positions: continue
                
                # Heuristic Buy Rule: RSI indicates momentum but not completely overbought
                if rsis[day_idx, sym_idx] > rsi_entry_thresh and rsis[day_idx-1, sym_idx] <= rsi_entry_thresh:
                    cur_price = day_closes[sym_idx]
                    if cur_price == 0: continue
                    
                    qty = int(buy_budget / cur_price)
                    if qty > 0 and (qty * cur_price) <= cash:
                        cost = qty * cur_price * 1.001 # slippage
                        cash -= cost
                        
                        # Set params
                        init_sl = cur_price * (1 - sl_pct)
                        init_tp = cur_price * (1 + tp_pct)
                        positions[sym_idx] = (qty, cur_price, cur_price, init_sl, init_tp)
                        slots -= 1

    # Liquidate remaining positions precisely at end
    final_value = cash
    last_closes = closes[-1]
    for sym_idx, pos in positions.items():
        qty = pos[0]
        final_value += qty * last_closes[sym_idx]
        
    return final_value

# 4. Bayesian Optimizer Objective
def objective(trial):
    # Suggest variables mathematically
    sl_pct          = trial.suggest_float("stop_loss_pct", 0.02, 0.10)
    tp_pct          = trial.suggest_float("take_profit_pct", 0.05, 0.30)
    trail_trigger   = trial.suggest_float("trail_activation_pct", 0.02, 0.10)
    trail_dist      = trial.suggest_float("trail_distance_pct", 0.01, 0.08)
    rsi_entry       = trial.suggest_int("rsi_buy_threshold", 45, 65)
    max_positions   = trial.suggest_int("max_positions", 3, 10)
    
    # Run absolute fast simulation
    final_portfolio_val = run_fast_backtest(
        sl_pct, tp_pct, trail_trigger, trail_dist, rsi_entry, max_positions
    )
    
    # Calculate Nominal CAGR over exact 10 Years
    nominal_cagr = (final_portfolio_val / INITIAL_CAPITAL) ** (1/10.0) - 1
    
    # Mathematical Real CAGR (Discounting Inflation)
    real_cagr = nominal_cagr - INFLATION_RATE
    
    # Maximize Real CAGR specifically
    return real_cagr


if __name__ == "__main__":
    print(f"[3/3] Commencing Bayesian Optimization across {TRIALS} Trial combinations...")
    start_time = time.time()
    
    # Minimize noise logs
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    
    # Create study
    study = optuna.create_study(direction="maximize")
    
    def print_callback(study, trial):
        if trial.number % 10 == 0:
            best_val = study.best_value
            print(f"  ... Iteration {trial.number}/{TRIALS} | Best Real CAGR: {best_val * 100:.2f}%")
            
    study.optimize(objective, n_trials=TRIALS, callbacks=[print_callback])
    
    elapsed = time.time() - start_time
    
    print("\n" + "="*50)
    print(" 🏆 BAYESIAN OPTIMIZATION COMPLETE 🏆 ")
    print("="*50)
    print(f"Time Taken:      {elapsed:.1f} seconds")
    print(f"Total Combos:    {TRIALS}")
    print(f"Inflation Rate:  {INFLATION_RATE*100:.1f}%")
    print("\n[BEST HYPERPARAMETERS TO HARDCODE INTO BOT]:")
    
    best_params = study.best_params
    for key, value in best_params.items():
        if "pct" in key:
            print(f"  {key.upper()}: {value*100:.2f}%")
        else:
            print(f"  {key.upper()}: {value}")
            
    print(f"\n=> 📈 Simulated FINAL REAL CAGR: +{study.best_value*100:.2f}% per year (AFTER inflation!)")
    print("==========================================================")
