"""
Vectrax Response Synthesizer
==============================
Compares N responses from parallel model queries and produces a
synthesized result with consensus scoring and divergence analysis.

Synthesis strategy:
  1. Content similarity (pairwise difflib SequenceMatcher)
  2. Latency efficiency
  3. Token efficiency
  4. Length-normalised quality heuristic
  5. Select best response + compute consensus
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.intelligence.parallel import ParallelQueryResult, ProviderResult

logger = logging.getLogger("vectrax.intelligence.synthesizer")


# ---------------------------------------------------------------------------
# Weights for synthesis scoring
# ---------------------------------------------------------------------------

W_SIMILARITY = 0.35     # how much the response agrees with others
W_LATENCY = 0.25        # faster is better
W_TOKENS = 0.20         # fewer tokens for similar content is better
W_LENGTH = 0.20         # reasonable length (not too short, not too long)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class ProviderScore:
    """Score breakdown for a single provider in the synthesis."""
    provider_name: str
    model: str
    similarity_score: float = 0.0
    latency_score: float = 0.0
    token_score: float = 0.0
    length_score: float = 0.0
    total_score: float = 0.0
    rank: int = 0

    def to_dict(self) -> Dict:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "scores": {
                "similarity": round(self.similarity_score, 4),
                "latency": round(self.latency_score, 4),
                "token_efficiency": round(self.token_score, 4),
                "length": round(self.length_score, 4),
                "total": round(self.total_score, 4),
            },
            "rank": self.rank,
        }


@dataclass
class DivergenceEntry:
    """Records divergence between two providers."""
    provider_a: str
    provider_b: str
    similarity_ratio: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "providers": [self.provider_a, self.provider_b],
            "similarity": round(self.similarity_ratio, 4),
        }


@dataclass
class SynthesizedResult:
    """Final output of the response synthesis process."""
    best_response: Optional[ProviderResult] = None
    best_provider: str = ""
    consensus_score: float = 0.0        # 0.0 = total disagreement, 1.0 = full consensus
    provider_scores: List[ProviderScore] = field(default_factory=list)
    divergence_map: List[DivergenceEntry] = field(default_factory=list)
    providers_compared: int = 0

    def to_dict(self) -> Dict:
        return {
            "best_provider": self.best_provider,
            "consensus_score": round(self.consensus_score, 4),
            "providers_compared": self.providers_compared,
            "provider_scores": [s.to_dict() for s in self.provider_scores],
            "divergence_map": [d.to_dict() for d in self.divergence_map],
        }


# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------

class ResponseSynthesizer:
    """
    Compares and synthesizes multi-model responses.

    Usage::

        synth = ResponseSynthesizer()
        result = synth.synthesize(parallel_query_result)
        print(result.best_provider, result.consensus_score)
    """

    def synthesize(self, pqr: ParallelQueryResult) -> SynthesizedResult:
        """
        Synthesize a ParallelQueryResult into a ranked, scored result.

        Args:
            pqr: The output of ParallelQueryEngine.query().

        Returns:
            SynthesizedResult with best_response, consensus, scores, divergences.
        """
        successful = pqr.successful_results

        if not successful:
            return SynthesizedResult(providers_compared=0)

        if len(successful) == 1:
            r = successful[0]
            score = ProviderScore(
                provider_name=r.provider_name,
                model=r.model,
                similarity_score=1.0,
                latency_score=1.0,
                token_score=1.0,
                length_score=1.0,
                total_score=1.0,
                rank=1,
            )
            return SynthesizedResult(
                best_response=r,
                best_provider=r.provider_name,
                consensus_score=1.0,
                provider_scores=[score],
                providers_compared=1,
            )

        # Step 1: Extract content texts
        contents: Dict[str, str] = {}
        for r in successful:
            contents[r.provider_name] = r.response.content if r.response else ""

        # Step 2: Pairwise similarity
        similarity_matrix = self._compute_similarity_matrix(contents)

        # Step 3: Per-provider average similarity
        avg_similarity: Dict[str, float] = {}
        for name in contents:
            others = [
                similarity_matrix[(name, other)]
                for other in contents if other != name
            ]
            avg_similarity[name] = sum(others) / len(others) if others else 0.0

        # Step 4: Compute normalised scores
        latencies = {r.provider_name: r.latency_ms for r in successful}
        tokens = {
            r.provider_name: (r.response.total_tokens or 0)
            for r in successful if r.response
        }
        lengths = {name: len(text) for name, text in contents.items()}

        max_latency = max(latencies.values()) or 1.0
        max_tokens = max(tokens.values()) or 1
        median_length = sorted(lengths.values())[len(lengths) // 2] or 1

        provider_scores: List[ProviderScore] = []
        for r in successful:
            name = r.provider_name

            sim_score = avg_similarity.get(name, 0.0)
            lat_score = 1.0 - (latencies.get(name, max_latency) / max_latency)
            tok_score = 1.0 - (tokens.get(name, max_tokens) / max_tokens) if max_tokens > 0 else 0.5
            # Length score: penalise being too far from median
            length_ratio = lengths.get(name, 0) / median_length if median_length > 0 else 1.0
            len_score = max(0.0, 1.0 - abs(1.0 - length_ratio) * 0.5)

            total = (
                sim_score * W_SIMILARITY
                + lat_score * W_LATENCY
                + tok_score * W_TOKENS
                + len_score * W_LENGTH
            )

            provider_scores.append(ProviderScore(
                provider_name=name,
                model=r.model,
                similarity_score=sim_score,
                latency_score=lat_score,
                token_score=tok_score,
                length_score=len_score,
                total_score=total,
            ))

        # Step 5: Rank
        provider_scores.sort(key=lambda s: -s.total_score)
        for i, s in enumerate(provider_scores):
            s.rank = i + 1

        # Step 6: Consensus score (average pairwise similarity)
        all_sims = list(similarity_matrix.values())
        consensus = sum(all_sims) / len(all_sims) if all_sims else 0.0

        # Step 7: Divergence map
        divergences = []
        for (a, b), sim in similarity_matrix.items():
            if a < b:  # avoid duplicates
                divergences.append(DivergenceEntry(
                    provider_a=a, provider_b=b, similarity_ratio=sim,
                ))
        divergences.sort(key=lambda d: d.similarity_ratio)

        # Best response
        best_name = provider_scores[0].provider_name
        best_result = next(r for r in successful if r.provider_name == best_name)

        return SynthesizedResult(
            best_response=best_result,
            best_provider=best_name,
            consensus_score=consensus,
            provider_scores=provider_scores,
            divergence_map=divergences,
            providers_compared=len(successful),
        )

    # -- internals ----------------------------------------------------------

    def _compute_similarity_matrix(
        self, contents: Dict[str, str]
    ) -> Dict[tuple, float]:
        """Compute pairwise similarity using difflib SequenceMatcher."""
        matrix: Dict[tuple, float] = {}
        names = list(contents.keys())

        for i, a in enumerate(names):
            for j, b in enumerate(names):
                if a == b:
                    matrix[(a, b)] = 1.0
                elif (b, a) in matrix:
                    matrix[(a, b)] = matrix[(b, a)]
                else:
                    ratio = difflib.SequenceMatcher(
                        None,
                        contents[a],
                        contents[b],
                    ).ratio()
                    matrix[(a, b)] = ratio

        return matrix
