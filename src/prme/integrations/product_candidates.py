"""Deterministic broad-recall candidate generation for product alignment."""

from __future__ import annotations

from collections import Counter, defaultdict
import math
import re
from typing import Iterable
import unicodedata
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prme.integrations.typesafe import ProductEntity


PRODUCT_CANDIDATE_POLICY = "product_tfidf_candidates_v1"
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "cd",
        "dvd",
        "edition",
        "for",
        "in",
        "mac",
        "new",
        "of",
        "old",
        "older",
        "pc",
        "rom",
        "software",
        "the",
        "to",
        "version",
        "win",
        "windows",
        "with",
    }
)


class ProductCandidateEntity(BaseModel):
    """One product node and the exact Jev-covered fields bound to it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: UUID
    product: ProductEntity
    catalog: str | None = Field(default=None, min_length=1, max_length=128)


class ProductAlignmentCandidate(BaseModel):
    """One deterministically ranked pair to send to a product advisor."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    policy: str = PRODUCT_CANDIDATE_POLICY
    left: ProductCandidateEntity
    right: ProductCandidateEntity
    score: float = Field(ge=0, le=1)
    shared_feature_count: int = Field(ge=1)
    selected_by: tuple[UUID, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def require_ordered_distinct_pair(self) -> "ProductAlignmentCandidate":
        if str(self.left.node_id) >= str(self.right.node_id):
            raise ValueError("Product candidates require ordered distinct node IDs")
        if any(node_id not in {self.left.node_id, self.right.node_id} for node_id in self.selected_by):
            raise ValueError("Product candidate selectors must belong to the pair")
        return self


def _words(value: str) -> list[str]:
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode()
        .casefold()
    )
    return _WORD.findall(ascii_value)


def _features(product: ProductEntity) -> Counter[str]:
    words = _words(product.name)
    manufacturer = _words(product.manufacturer)
    features = Counter(f"word:{word}" for word in words if word not in _STOPWORDS)
    features.update(
        f"bigram:{words[index]}_{words[index + 1]}"
        for index in range(len(words) - 1)
    )
    features.update(
        f"manufacturer:{word}" for word in manufacturer if len(word) > 1
    )
    return features


def rank_product_alignment_candidates(
    products: Iterable[ProductCandidateEntity | dict],
    *,
    top_k: int = 5,
    min_score: float = 0.1,
    cross_catalog_only: bool = False,
) -> list[ProductAlignmentCandidate]:
    """Generate a deterministic sparse TF-IDF top-k union for Jev review.

    The ranker is deliberately broad. It does not establish identity and never
    mutates graph state. Jev or another reviewed advisor must decide each pair.
    """
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise ValueError("top_k must be between 1 and 100")
    if (
        type(min_score) not in (int, float)
        or not math.isfinite(min_score)
        or not 0 <= min_score <= 1
    ):
        raise ValueError("min_score must be finite and between zero and one")
    records = [ProductCandidateEntity.model_validate(item) for item in products]
    if len({item.node_id for item in records}) != len(records):
        raise ValueError("Product candidate node IDs must be unique")
    if cross_catalog_only and any(item.catalog is None for item in records):
        raise ValueError("Cross-catalog candidates require every catalog label")
    if len(records) < 2:
        return []

    by_id = {item.node_id: item for item in records}
    feature_counts = {item.node_id: _features(item.product) for item in records}
    document_frequency: Counter[str] = Counter()
    for features in feature_counts.values():
        document_frequency.update(features)
    size = len(records)
    inverse_frequency = {
        feature: math.log((size + 1) / (count + 1)) + 1
        for feature, count in document_frequency.items()
    }
    vectors: dict[UUID, tuple[dict[str, float], float]] = {}
    inverted: defaultdict[str, set[UUID]] = defaultdict(set)
    for node_id, counts in feature_counts.items():
        vector = {
            feature: (1 + math.log(count)) * inverse_frequency[feature]
            for feature, count in counts.items()
        }
        norm = math.sqrt(sum(value * value for value in vector.values()))
        vectors[node_id] = (vector, norm)
        for feature in vector:
            inverted[feature].add(node_id)

    selected: defaultdict[tuple[UUID, UUID], set[UUID]] = defaultdict(set)
    pair_scores: dict[tuple[UUID, UUID], tuple[float, int]] = {}
    for node_id in sorted(by_id, key=str):
        left = by_id[node_id]
        vector, norm = vectors[node_id]
        candidate_ids: set[UUID] = set()
        for feature in vector:
            candidate_ids.update(inverted[feature])
        ranked = []
        for other_id in candidate_ids:
            if other_id == node_id:
                continue
            right = by_id[other_id]
            if cross_catalog_only and left.catalog == right.catalog:
                continue
            other_vector, other_norm = vectors[other_id]
            if not norm or not other_norm:
                continue
            shared = set(vector).intersection(other_vector)
            score = sum(vector[key] * other_vector[key] for key in shared) / (
                norm * other_norm
            )
            if score < min_score:
                continue
            ranked.append((-score, str(other_id), other_id, len(shared)))
        ranked.sort()
        for negative_score, _, other_id, shared_count in ranked[:top_k]:
            pair = (
                (node_id, other_id)
                if str(node_id) < str(other_id)
                else (other_id, node_id)
            )
            selected[pair].add(node_id)
            pair_scores[pair] = (-negative_score, shared_count)

    result = []
    for pair, selectors in selected.items():
        left_id, right_id = pair
        score, shared_count = pair_scores[pair]
        result.append(
            ProductAlignmentCandidate(
                left=by_id[left_id],
                right=by_id[right_id],
                score=round(score, 10),
                shared_feature_count=shared_count,
                selected_by=tuple(sorted(selectors, key=str)),
            )
        )
    return sorted(
        result,
        key=lambda item: (-item.score, str(item.left.node_id), str(item.right.node_id)),
    )


__all__ = [
    "PRODUCT_CANDIDATE_POLICY",
    "ProductAlignmentCandidate",
    "ProductCandidateEntity",
    "rank_product_alignment_candidates",
]
