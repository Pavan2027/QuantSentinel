"""
sentiment/finbert_model.py
---------------------------
FinBERT sentiment inference for financial headlines.

Model: ProsusAI/finbert (HuggingFace)
  - Trained specifically on financial text
  - 3 classes: positive, negative, neutral
  - Much better than general-purpose BERT on earnings/market text

Design decisions:
  - Runs on CPU by default (no GPU required)
  - Batch inference for efficiency (don't call model per headline)
  - Confidence threshold: 0.65 (ignore low-confidence predictions)
  - Below-threshold predictions → treated as neutral (0.5)
  - Model is loaded once and cached in memory (singleton pattern)
  - Results cached in SQLite to avoid re-running on same headlines

Output score convention:
  - 1.0 = strongly positive (buy signal boost)
  - 0.5 = neutral (no signal)
  - 0.0 = strongly negative (sell signal / avoid buying)

Usage:
    model = FinBERTModel()
    scores = model.score_headlines(["RELIANCE beats Q3 estimates", "Market sell-off"])
    # Returns: [0.82, 0.15]
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from config.settings import FINBERT_THRESHOLD
from utils.logger import get_logger

log = get_logger("finbert")

# HuggingFace model identifier
FINBERT_MODEL_NAME = "ProsusAI/finbert"

# Singleton — loaded once per process
_model = None
_tokenizer = None
_device = None


def _get_device():
    """Return the best available device: CUDA > MPS (Apple) > CPU."""
    try:
        import torch
        if torch.cuda.is_available():
            log.info("FinBERT: using CUDA GPU")
            return torch.device("cuda")
        # Apple Silicon
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            log.info("FinBERT: using MPS (Apple Silicon)")
            return torch.device("mps")
    except ImportError:
        pass
    log.info("FinBERT: using CPU")
    import torch
    return torch.device("cpu")


def _load_model():
    """Load FinBERT model and tokenizer. Called once, cached globally."""
    global _model, _tokenizer, _device

    if _model is not None:
        return _model, _tokenizer, _device

    log.info(f"Loading FinBERT model: {FINBERT_MODEL_NAME} ...")
    log.info("This will take 30–60 seconds on first run and download ~500MB")

    try:
        from transformers import BertTokenizer, BertForSequenceClassification
        import torch

        _device   = _get_device()
        _tokenizer = BertTokenizer.from_pretrained(FINBERT_MODEL_NAME)
        _model     = BertForSequenceClassification.from_pretrained(FINBERT_MODEL_NAME)
        _model.to(_device)
        _model.eval()

        log.info(f"FinBERT loaded successfully on {_device}")
        return _model, _tokenizer, _device

    except ImportError:
        log.error(
            "transformers or torch not installed. "
            "Run: pip install transformers torch"
        )
        raise
    except Exception as e:
        log.error(f"Failed to load FinBERT: {e}")
        raise


def _headline_hash(text: str) -> str:
    """SHA-256 hash of normalized headline for cache keying."""
    return hashlib.sha256(text.lower().strip().encode()).hexdigest()[:20]


def _raw_scores_to_sentiment(logits, label_order: list[str]) -> dict:
    """
    Convert raw model logits to a structured sentiment result.

    FinBERT label order: [positive, negative, neutral]
    Returns:
        {
          "label":      "positive" | "negative" | "neutral",
          "confidence": float (0–1),
          "score":      float (0–1),  # our normalized signal
          "raw":        {"positive": float, "negative": float, "neutral": float}
        }
    """
    import torch
    probs = torch.softmax(logits, dim=-1).squeeze().tolist()
    if isinstance(probs, float):
        probs = [probs]

    raw = {label: round(float(prob), 4) for label, prob in zip(label_order, probs)}
    best_idx  = probs.index(max(probs))
    best_label = label_order[best_idx]
    confidence = probs[best_idx]

    # Convert to [0, 1] signal:
    # positive → 0.5 + 0.5 * confidence  (0.5–1.0)
    # negative → 0.5 - 0.5 * confidence  (0.0–0.5)
    # neutral  → 0.5 exactly
    if best_label == "positive":
        signal_score = 0.5 + 0.5 * confidence
    elif best_label == "negative":
        signal_score = 0.5 - 0.5 * confidence
    else:
        signal_score = 0.5

    return {
        "label":      best_label,
        "confidence": round(confidence, 4),
        "score":      round(signal_score, 4),
        "raw":        raw,
    }


class FinBERTModel:
    """
    FinBERT sentiment scorer.
    Instantiate once and reuse across the pipeline.
    """

    # FinBERT's label order (fixed by ProsusAI/finbert)
    LABEL_ORDER = ["positive", "negative", "neutral"]

    def __init__(self,
                 threshold: float = None,
                 batch_size: int = 16,
                 max_length: int = 128):
        """
        Args:
            threshold:  Minimum confidence to trust a prediction.
                        Below threshold → treated as neutral (score=0.5).
                        Defaults to settings.FINBERT_THRESHOLD (0.65).
            batch_size: Headlines per inference batch. 16 is safe for CPU.
            max_length: Max tokenizer length. 128 covers most headlines.
        """
        self.threshold  = threshold if threshold is not None else FINBERT_THRESHOLD
        self.batch_size = batch_size
        self.max_length = max_length
        self._cache: dict[str, dict] = {}   # in-memory per-session cache

    def _load(self):
        """Lazy-load model on first inference call."""
        return _load_model()

    def score_headline(self, headline: str) -> dict:
        """
        Score a single headline.

        Returns:
            {
              "headline":   str,
              "label":      "positive" | "negative" | "neutral",
              "confidence": float,
              "score":      float (0–1),
              "trusted":    bool,   # True if confidence >= threshold
              "raw":        dict
            }
        """
        results = self.score_headlines([headline])
        return results[0] if results else self._neutral_result(headline)

    def score_headlines(self, headlines: list[str]) -> list[dict]:
        """
        Score a batch of headlines efficiently.

        Args:
            headlines: List of headline strings

        Returns:
            List of result dicts (same order as input), each with:
            headline, label, confidence, score, trusted, raw
        """
        if not headlines:
            return []

        import torch

        model, tokenizer, device = self._load()
        results = []

        # Process in batches
        for batch_start in range(0, len(headlines), self.batch_size):
            batch = headlines[batch_start : batch_start + self.batch_size]
            batch_results = self._score_batch(batch, model, tokenizer, device)
            results.extend(batch_results)

        return results

    def _score_batch(self, headlines: list[str],
                     model, tokenizer, device) -> list[dict]:
        """Run inference on a single batch."""
        import torch

        # Check cache first
        results     = [None] * len(headlines)
        uncached_idx = []
        uncached_txt = []

        for i, h in enumerate(headlines):
            key = _headline_hash(h)
            if key in self._cache:
                results[i] = {**self._cache[key], "headline": h}
            else:
                uncached_idx.append(i)
                uncached_txt.append(h)

        if not uncached_txt:
            return results

        # Tokenize
        encoded = tokenizer(
            uncached_txt,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        encoded = {k: v.to(device) for k, v in encoded.items()}

        # Inference
        with torch.no_grad():
            outputs = model(**encoded)

        logits = outputs.logits

        # Parse each result
        for batch_idx, orig_idx in enumerate(uncached_idx):
            headline = headlines[orig_idx]
            parsed   = _raw_scores_to_sentiment(
                logits[batch_idx], self.LABEL_ORDER
            )

            # Apply confidence threshold
            trusted = parsed["confidence"] >= self.threshold
            if not trusted:
                parsed["score"] = 0.5   # treat low-confidence as neutral

            result = {
                "headline":   headline,
                "label":      parsed["label"],
                "confidence": parsed["confidence"],
                "score":      parsed["score"],
                "trusted":    trusted,
                "raw":        parsed["raw"],
            }

            # Cache by hash
            key = _headline_hash(headline)
            self._cache[key] = {k: v for k, v in result.items() if k != "headline"}
            results[orig_idx] = result

        return results

    def score_stock_news(self, news_items: list[dict]) -> list[dict]:
        """
        Score a list of news records from news_provider.get_news_for_stock().

        Adds "sentiment_score" and "sentiment_label" fields to each record.
        Respects staleness decay — low decay_weight items are scored but
        their contribution is reduced in the aggregator.

        Args:
            news_items: List of dicts with at least "headline" key

        Returns:
            Same list with sentiment fields added
        """
        if not news_items:
            return []

        headlines = [item.get("headline", "") for item in news_items]
        scored    = self.score_headlines(headlines)

        enriched = []
        for item, result in zip(news_items, scored):
            enriched.append({
                **item,
                "sentiment_score": result["score"],
                "sentiment_label": result["label"],
                "sentiment_confidence": result["confidence"],
                "sentiment_trusted": result["trusted"],
            })

        return enriched

    @staticmethod
    def _neutral_result(headline: str) -> dict:
        return {
            "headline":   headline,
            "label":      "neutral",
            "confidence": 0.0,
            "score":      0.5,
            "trusted":    False,
            "raw":        {"positive": 0.33, "negative": 0.33, "neutral": 0.34},
        }


def is_finbert_available() -> bool:
    """
    Check if transformers and torch are installed without loading the model.
    Used to gracefully degrade to neutral sentiment if dependencies missing.
    """
    try:
        import transformers
        import torch
        return True
    except ImportError:
        return False