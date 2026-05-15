import sqlite3
import math
import os
import sys

# Add root to sys.path so we can import config
sys.path.append(os.path.dirname(__file__))
from config.settings import PAPER_CAPITAL_INR

def fix_db():
    conn = sqlite3.connect('data/bot.db')
    conn.row_factory = sqlite3.Row
    
    # 1. Check if cash is broken
    row = conn.execute("SELECT value FROM control_flags WHERE key='PAPER_CASH'").fetchone()
    if not row:
        print("No PAPER_CASH found. Exiting.")
        return
        
    cash_val = row["value"]
    print(f"Current PAPER_CASH string in DB: {cash_val}")
    
    try:
        f_cash = float(cash_val)
        if not math.isnan(f_cash):
            print(f"Cash is valid ({f_cash}). No fix needed.")
            # but let's force fix it just in case
    except ValueError:
        pass
        
    print("Computing correct cash balance...")
    
    # Capital
    capital = PAPER_CAPITAL_INR
    
    # Sum of Realized PnL from all sell trades
    pnl_row = conn.execute("SELECT SUM(pnl) as total_pnl FROM trades WHERE action='SELL'").fetchone()
    total_pnl = float(pnl_row["total_pnl"]) if pnl_row and pnl_row["total_pnl"] else 0.0
    
    # Sum of invested amount in open positions
    # (cost to enter = qty * avg_entry_price)
    pos_row = conn.execute("SELECT SUM(qty * avg_entry_price) as invested FROM positions").fetchone()
    invested = float(pos_row["invested"]) if pos_row and pos_row["invested"] else 0.0
    
    # Total brokerage and slippage and exchange fees are already factored into the entry cost 
    # (actually in paper_trader, total_cost = trade_value + brokerage + exchange_charge, but avg_entry_price is just fill_price, which includes slippage but NOT brokerage/exchange)
    # Wait, let's just trace the cash delta for every trade.
    
    cash = capital
    trades = conn.execute("SELECT action, qty, price, brokerage, slippage, pnl FROM trades ORDER BY created_at").fetchall()
    
    for t in trades:
        # Re-run paper trader math
        action = t["action"]
        qty = t["qty"]
        price = t["price"]  # fill_price
        brokerage = t["brokerage"]
        
        trade_value = price * qty
        if action == "BUY":
            from config.settings import EXCHANGE_TXN_RATE
            exchange_charge = trade_value * EXCHANGE_TXN_RATE
            total_cost = trade_value + brokerage + exchange_charge
            cash -= total_cost
        elif action == "SELL":
            from config.settings import STT_RATE_DELIVERY, EXCHANGE_TXN_RATE
            stt = trade_value * STT_RATE_DELIVERY
            exchange_charge = trade_value * EXCHANGE_TXN_RATE
            total_deductions = brokerage + stt + exchange_charge
            net_proceeds = trade_value - total_deductions
            cash += net_proceeds
            
    if math.isnan(cash):
        print("Error: Computed cash is NaN! One of the trades has missing data.")
        return
        
    print(f"Computed correct cash: ₹{cash:,.2f}")
    
    conn.execute(
        "UPDATE control_flags SET value = ? WHERE key = 'PAPER_CASH'",
        (str(round(cash, 2)),)
    )
    conn.commit()
    print("Database fixed successfully.")

if __name__ == "__main__":
    fix_db()
