from backtest.backtester import BacktestEngine, BacktestConfig
from data.news_provider import get_news_for_stock
from sentiment.finbert_model import FinBERTModel
from sentiment.aggregator import aggregate_universe_sentiment, get_sentiment_scores_only

SYMBOLS = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK']

# Pre-fetch sentiment for the universe
print('Loading sentiment...')
model = FinBERTModel()
stock_news = {sym: get_news_for_stock(sym) for sym in SYMBOLS}
universe_sentiment = aggregate_universe_sentiment(model, stock_news)
sentiment_scores = get_sentiment_scores_only(universe_sentiment)

print('Sentiment scores:')
for sym, score in sorted(sentiment_scores.items(), key=lambda x: -x[1]):
    from sentiment.aggregator import describe_sentiment
    print(f'  {sym:12s} {score:.3f}  {describe_sentiment(score)}')

# Run backtest with real sentiment
cfg = BacktestConfig(
    symbols=SYMBOLS,
    start_date='2024-07-01',
    end_date='2024-12-31',
    initial_capital=100_000.0,
    max_positions=3,
    sentiment_scores=sentiment_scores,
)
engine = BacktestEngine(cfg)
results = engine.run()
m = results['metrics']

print(f'''
  Backtest with FinBERT Sentiment:
  Return:    {m["total_return_pct"]:+.1f}%
  Sharpe:    {m["sharpe_ratio"]:.2f}
  MaxDD:     {m["max_drawdown_pct"]:.1f}%
  Trades:    {m["total_trades"]}
  WinRate:   {m.get("win_rate", 0)*100:.1f}%
  Verdict:   {m["go_nogo"]}
''')

from backtest.report_generator import generate_html_report
path = generate_html_report(results)
print(f'Report: {path}')