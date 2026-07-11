"""
matcher.py — 3-layer vocabulary matching engine.

Matching order:
    Layer 1 (Exact):    Perfect synonym match in the vocabulary dictionary.
    Layer 2 (Fuzzy):    thefuzz token_sort_ratio > 75.
    Layer 3 (Semantic): spacy en_core_web_md vector similarity > 0.65.

If all layers fail, returns {"matched": None, "closest": [best_guess]}.

Usage:
    from matcher import VocabularyMatcher
    from vocabulary import ROOMS

    room_matcher = VocabularyMatcher(ROOMS)
    result = room_matcher.match("balkony")
    # → {"matched": "balcony", "confidence": 88, "layer": "fuzzy"}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — fail gracefully if not installed
# ---------------------------------------------------------------------------

_THEFUZZ_AVAILABLE = False
_SPACY_AVAILABLE = False
_NLP_MODEL = None

try:
    from thefuzz import fuzz
    _THEFUZZ_AVAILABLE = True
except ImportError:
    logger.warning("thefuzz not installed — Layer 2 (fuzzy) matching disabled")

try:
    import spacy
    _SPACY_AVAILABLE = True
except ImportError:
    logger.warning("spacy not installed — Layer 3 (semantic) matching disabled")


def _load_spacy_model():
    """Load spacy model lazily on first semantic match attempt."""
    global _NLP_MODEL
    if _NLP_MODEL is not None:
        return _NLP_MODEL

    if not _SPACY_AVAILABLE:
        return None

    try:
        _NLP_MODEL = spacy.load("en_core_web_md")
        logger.info("Loaded spacy en_core_web_md for semantic matching")
        return _NLP_MODEL
    except OSError:
        logger.warning(
            "spacy model en_core_web_md not found. "
            "Install with: python -m spacy download en_core_web_md"
        )
        return None


# ---------------------------------------------------------------------------
# Match result
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """Result of a vocabulary match attempt."""
    matched: Optional[str] = None
    canonical: Optional[str] = None
    confidence: int = 0
    layer: Optional[str] = None  # "exact", "fuzzy", "semantic"
    closest: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "matched": self.matched,
            "canonical": self.canonical,
            "confidence": self.confidence,
            "layer": self.layer,
        }
        if not self.matched:
            d["closest"] = self.closest
        return d

    @property
    def found(self) -> bool:
        return self.matched is not None


# ---------------------------------------------------------------------------
# VocabularyMatcher
# ---------------------------------------------------------------------------

class VocabularyMatcher:
    """
    3-layer vocabulary matcher for architectural term recognition.

    Parameters
    ----------
    vocabulary : dict[str, list[str]]
        Maps canonical terms to lists of known synonyms.
        Example: {"balcony": ["balcony", "balkony", "verandah", ...]}
    fuzzy_threshold : int
        Minimum thefuzz score for Layer 2 match (default: 75).
    semantic_threshold : float
        Minimum spacy similarity for Layer 3 match (default: 0.65).
    """

    def __init__(
        self,
        vocabulary: Dict[str, List[str]],
        fuzzy_threshold: int = 75,
        semantic_threshold: float = 0.65,
    ):
        self.vocabulary = vocabulary
        self.fuzzy_threshold = fuzzy_threshold
        self.semantic_threshold = semantic_threshold

        # Build reverse index: synonym → canonical
        self._reverse: Dict[str, str] = {}
        for canonical, synonyms in vocabulary.items():
            for syn in synonyms:
                self._reverse[syn.lower().strip()] = canonical

        # All canonical terms (for fuzzy + semantic comparison)
        self._canonical_terms = list(vocabulary.keys())

        # All synonyms flat (for fuzzy comparison)
        self._all_synonyms: List[Tuple[str, str]] = []  # (synonym, canonical)
        for canonical, synonyms in vocabulary.items():
            for syn in synonyms:
                self._all_synonyms.append((syn.lower().strip(), canonical))

    def match(self, user_term: str) -> MatchResult:
        """
        Attempt to match a user term through 3 layers.

        Returns a MatchResult with the canonical match or closest guess.
        """
        if not user_term or not user_term.strip():
            return MatchResult(closest=[])

        term = user_term.lower().strip()

        # Layer 1: Exact synonym match
        result = self._layer_exact(term)
        if result.found:
            return result

        # Layer 2: Fuzzy string matching
        result = self._layer_fuzzy(term)
        if result.found:
            return result

        # Layer 3: Semantic similarity
        result = self._layer_semantic(term)
        if result.found:
            return result

        # All layers failed — return best fuzzy guess
        return self._build_failure(term)

    def match_multi(self, terms: List[str]) -> List[MatchResult]:
        """Match multiple terms at once."""
        return [self.match(t) for t in terms]

    # ----- Layer 1: Exact -----

    def _layer_exact(self, term: str) -> MatchResult:
        """Check if term exactly matches any synonym."""
        canonical = self._reverse.get(term)
        if canonical is not None:
            return MatchResult(
                matched=term,
                canonical=canonical,
                confidence=100,
                layer="exact",
            )
        return MatchResult()

    # ----- Layer 2: Fuzzy -----

    def _layer_fuzzy(self, term: str) -> MatchResult:
        """Use thefuzz token_sort_ratio to find close matches."""
        if not _THEFUZZ_AVAILABLE:
            return MatchResult()

        best_score = 0
        best_synonym = ""
        best_canonical = ""

        for synonym, canonical in self._all_synonyms:
            score = fuzz.token_sort_ratio(term, synonym)
            if score > best_score:
                best_score = score
                best_synonym = synonym
                best_canonical = canonical

        if best_score >= self.fuzzy_threshold:
            return MatchResult(
                matched=best_synonym,
                canonical=best_canonical,
                confidence=best_score,
                layer="fuzzy",
            )

        return MatchResult()

    # ----- Layer 3: Semantic -----

    def _layer_semantic(self, term: str) -> MatchResult:
        """Use spacy word vectors to find semantically similar terms."""
        nlp = _load_spacy_model()
        if nlp is None:
            return MatchResult()

        term_doc = nlp(term)

        # Skip if no vector
        if not term_doc.has_vector or term_doc.vector_norm == 0:
            return MatchResult()

        best_sim = 0.0
        best_canonical = ""

        for canonical in self._canonical_terms:
            canonical_doc = nlp(canonical)
            if not canonical_doc.has_vector or canonical_doc.vector_norm == 0:
                continue
            sim = term_doc.similarity(canonical_doc)
            if sim > best_sim:
                best_sim = sim
                best_canonical = canonical

        if best_sim >= self.semantic_threshold:
            return MatchResult(
                matched=term,
                canonical=best_canonical,
                confidence=int(best_sim * 100),
                layer="semantic",
            )

        return MatchResult()

    # ----- Failure fallback -----

    def _build_failure(self, term: str) -> MatchResult:
        """Build a failure result with the closest guess."""
        closest: List[str] = []

        if _THEFUZZ_AVAILABLE:
            # Get top fuzzy guess
            scored = []
            for synonym, canonical in self._all_synonyms:
                score = fuzz.token_sort_ratio(term, synonym)
                scored.append((score, canonical))
            scored.sort(key=lambda x: x[0], reverse=True)

            # Deduplicate canonicals
            seen = set()
            for score, canonical in scored[:3]:
                if canonical not in seen:
                    closest.append(canonical)
                    seen.add(canonical)
        else:
            # Without fuzzy, just suggest first 3 canonical terms
            closest = self._canonical_terms[:3]

        return MatchResult(closest=closest)


# ---------------------------------------------------------------------------
# Multi-vocabulary matcher — runs a term against multiple dictionaries
# ---------------------------------------------------------------------------

class MultiVocabularyMatcher:
    """
    Runs a term through multiple VocabularyMatcher instances and returns
    results keyed by category.

    Parameters
    ----------
    matchers : dict[str, VocabularyMatcher]
        Maps category name to matcher.
        Example: {"rooms": room_matcher, "materials": material_matcher}
    """

    def __init__(self, matchers: Dict[str, VocabularyMatcher]):
        self.matchers = matchers

    def match(self, user_term: str) -> Dict[str, MatchResult]:
        """
        Try matching a term in all categories.
        Returns dict of category → MatchResult (only matched ones).
        """
        results: Dict[str, MatchResult] = {}
        for category, matcher in self.matchers.items():
            result = matcher.match(user_term)
            if result.found:
                results[category] = result
        return results

    def match_best(self, user_term: str) -> Tuple[Optional[str], MatchResult]:
        """
        Find the best match across all categories.
        Returns (category, result) or (None, empty_result).
        """
        best_category: Optional[str] = None
        best_result = MatchResult()

        for category, matcher in self.matchers.items():
            result = matcher.match(user_term)
            if result.found and result.confidence > best_result.confidence:
                best_category = category
                best_result = result

        return best_category, best_result
