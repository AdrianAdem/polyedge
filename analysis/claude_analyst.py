"""Two-tier LLM analysis pipeline.

Tier 1 (Haiku) is a cheap relevance filter that runs on every scanned market.
Tier 2 (Sonnet) only runs on markets Haiku flags as both relevant and urgent,
which is what keeps per-scan API cost bounded. Both tiers log tokens, latency
and cost to SQLite so spend is auditable per trade.
"""

from __future__ import annotations

import json
import time

import anthropic
import structlog

from storage.db import Database
from storage.models import HaikuSignal, ScanResult, SonnetSignal, TradeAction

logger = structlog.get_logger()

HAIKU_SYSTEM_PROMPT = """Du bist ein Marktanalyse-Filter. Du bekommst einen Polymarket-Markt
und aktuelle Nachrichtenlage. Antworte NUR mit JSON.

Aufgabe: Bewerte ob aktuelle Nachrichten die Wahrscheinlichkeit dieses Marktes
signifikant von seinem aktuellen Preis abweichen lassen.

Antwortformat:
{"relevant": true/false, "direction": "higher"/"lower"/"unchanged", "urgency": 1-5}

Regeln:
- relevant=true NUR wenn du konkrete neue Information hast die den Preis bewegen sollte
- urgency 5 = sofort handeln, 1 = beobachten
- Wenn du unsicher bist: relevant=false"""

SONNET_SYSTEM_PROMPT = """Du bist ein quantitativer Analyst für Prediction Markets.

Kontext:
- Polymarket-Markt: {market_question}
- Aktueller Preis: {current_price} (= implizierte Wahrscheinlichkeit {implied_prob}%)
- Ablauf: {expiry}
- Aktuelle Nachrichten: {news_context}
- Makrodaten: {macro_context}
- Binance BTC/ETH Daten (falls Crypto-Markt): {crypto_context}

Aufgabe:
1. Schätze die REALE Wahrscheinlichkeit basierend auf allen verfügbaren Daten
2. Berechne den Edge: real_probability - current_price
3. Bewerte deine Confidence (0.0-1.0)
4. Empfehle: BUY_YES, BUY_NO, oder SKIP

Antworte NUR mit JSON:
{{
  "real_probability": 0.XX,
  "edge": 0.XX,
  "confidence": 0.XX,
  "action": "BUY_YES" | "BUY_NO" | "SKIP",
  "reasoning": "Kurze Begründung in 2-3 Sätzen",
  "key_factors": ["Faktor 1", "Faktor 2", "Faktor 3"]
}}

Regeln:
- Edge muss > 0.10 sein für BUY-Signal
- Confidence muss > 0.7 sein für BUY-Signal
- Wenn Daten widersprüchlich: SKIP
- Wenn Markt illiquide (spread > 5%): SKIP
- Sei konservativ. Lieber ein Trade zu wenig als einer zu viel."""

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-5"

COST_PER_1K: dict[str, dict[str, float]] = {
    HAIKU_MODEL: {"input": 0.001, "output": 0.005},
    SONNET_MODEL: {"input": 0.003, "output": 0.015},
}


class ClaudeAnalyst:
    def __init__(self, api_key: str, db: Database) -> None:
        # SDK retries with exponential backoff on 429/5xx/timeouts
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=4)
        self.db = db

    async def quick_filter(self, scan: ScanResult, news_context: str) -> HaikuSignal:
        """Tier 1: decide whether a market deserves expensive analysis.

        Fails closed — any API or parse error returns ``relevant=False`` so a
        broken response can never escalate to a Tier 2 call or a trade.
        """
        user_msg = (
            f"Markt: {scan.market.question}\n"
            f"Preis: {scan.current_price:.2f} ({scan.current_price * 100:.0f}%)\n"
            f"Volumen: ${scan.volume:,.0f}\n"
            f"Ablauf in: {scan.time_to_expiry_hours:.0f}h\n"
            f"Kategorie: {scan.category}\n\n"
            f"Aktuelle Nachrichten:\n{news_context}"
        )

        start = time.time()
        try:
            resp = await self.client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=200,
                system=[
                    {
                        "type": "text",
                        "text": HAIKU_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_msg}],
            )

            latency = (time.time() - start) * 1000
            cost = self._calc_cost(HAIKU_MODEL, resp.usage.input_tokens, resp.usage.output_tokens)

            await self.db.log_api_call(
                model=HAIKU_MODEL,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cost_usd=cost,
                latency_ms=latency,
            )

            text = resp.content[0].text.strip()
            data = json.loads(text)

            return HaikuSignal(
                relevant=data.get("relevant", False),
                direction=data.get("direction", "unchanged"),
                urgency=data.get("urgency", 1),
            )

        except (json.JSONDecodeError, anthropic.APIError) as e:
            logger.error("haiku_filter_error", error=str(e), market=scan.market.question)
            return HaikuSignal(relevant=False, direction="unchanged", urgency=0)

    async def deep_analysis(
        self,
        scan: ScanResult,
        news_context: str,
        macro_context: str,
        crypto_context: str,
    ) -> SonnetSignal:
        """Tier 2: estimate true probability and recommend an action.

        Also fails closed: a malformed response becomes a SKIP verdict rather
        than a trade on garbage numbers.
        """
        system = SONNET_SYSTEM_PROMPT.format(
            market_question=scan.market.question,
            current_price=f"{scan.current_price:.2f}",
            implied_prob=f"{scan.current_price * 100:.0f}",
            expiry=scan.market.end_date.isoformat(),
            news_context=news_context,
            macro_context=macro_context,
            crypto_context=crypto_context,
        )

        user_msg = (
            f"Analysiere diesen Markt und gib deine Einschätzung als JSON.\n"
            f"Spread: {scan.market.spread:.4f}\n"
            f"Volumen: ${scan.volume:,.0f}\n"
            f"Stunden bis Ablauf: {scan.time_to_expiry_hours:.0f}"
        )

        start = time.time()
        try:
            resp = await self.client.messages.create(
                model=SONNET_MODEL,
                max_tokens=500,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_msg}],
            )

            latency = (time.time() - start) * 1000
            cost = self._calc_cost(SONNET_MODEL, resp.usage.input_tokens, resp.usage.output_tokens)

            await self.db.log_api_call(
                model=SONNET_MODEL,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cost_usd=cost,
                latency_ms=latency,
            )

            text = resp.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            data = json.loads(text)
            action_str = data.get("action", "SKIP")
            try:
                action = TradeAction(action_str)
            except ValueError:
                action = TradeAction.SKIP

            signal = SonnetSignal(
                real_probability=float(data.get("real_probability", 0)),
                edge=float(data.get("edge", 0)),
                confidence=float(data.get("confidence", 0)),
                action=action,
                reasoning=data.get("reasoning", ""),
                key_factors=data.get("key_factors", []),
            )

            await self.db.log_signal(
                market_id=scan.market.market_id,
                market_question=scan.market.question,
                category=scan.category,
                haiku_relevant=True,
                haiku_urgency=0,
                sonnet_real_prob=signal.real_probability,
                edge=signal.edge,
                confidence=signal.confidence,
                action=signal.action.value,
                reasoning=signal.reasoning,
                key_factors=signal.key_factors,
            )

            logger.info(
                "sonnet_analysis",
                market=scan.market.question[:50],
                action=signal.action.value,
                edge=signal.edge,
                confidence=signal.confidence,
            )
            return signal

        except (json.JSONDecodeError, anthropic.APIError) as e:
            logger.error("sonnet_analysis_error", error=str(e), market=scan.market.question)
            return SonnetSignal(
                real_probability=0,
                edge=0,
                confidence=0,
                action=TradeAction.SKIP,
                reasoning=f"Analysis error: {e}",
            )

    def _calc_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        rates = COST_PER_1K.get(model, {"input": 0.003, "output": 0.015})
        return (input_tokens / 1000 * rates["input"]) + (output_tokens / 1000 * rates["output"])
