"""Optional TypeSafe Jev advisor for software-product entity alignment.

The confirmed protocol compares one caller-supplied pair.  It can recommend an
unverified alias proposal.  A separate explicit workflow can publish that
proposal with complete evidence, but neither path authorizes an identity merge.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from prme.models.derivation import canonical_hash, node_checksum
from prme.organizer.merge_policy import alias_pair_allowed
from prme.storage.alias_proposal import AliasProposalEvidence
from prme.types import NodeType

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.engine import MemoryEngine


JEV_PRODUCT_ALIGNMENT_PROTOCOL: Literal["jev_product_alignment_v1"] = (
    "jev_product_alignment_v1"
)
JEV_PRODUCT_ALIGNMENT_MODEL: Literal["jev-1.13.0"] = "jev-1.13.0"
JEV_PRODUCT_ALIGNMENT_API_URL = "https://api.typesafe.ai/v1/systemone"
JEV_PRODUCT_ALIGNMENT_THRESHOLD = 1.5
JEV_PRODUCT_ALIGNMENT_LEVELS = (
    "They are different catalog products.",
    (
        "They are related but not safely identical: a different version, edition, "
        "bundle, platform, capacity, pack size, or an ambiguous listing."
    ),
    (
        "They are the same catalog product despite formatting differences, "
        "abbreviations, price variation, or a missing optional field."
    ),
)


def _protocol_questions() -> dict[str, Any]:
    """Build a fresh copy so callers cannot mutate the request protocol."""
    return {
        "link_state": {
            "type": "score",
            "instructions": "How do the two entity descriptions relate as products?",
            "criteria": list(JEV_PRODUCT_ALIGNMENT_LEVELS),
        },
        "same_name": {
            "type": "noul",
            "instructions": "Do the two listings name the same product?",
        },
        "same_manufacturer": {
            "type": "noul",
            "instructions": "Are the two listings from the same manufacturer?",
        },
        "compatible_price": {
            "type": "noul",
            "instructions": (
                "Could the stated prices plausibly describe the same product listing, "
                "allowing a missing price or ordinary price variation?"
            ),
        },
    }


# This copy is for inspection. Requests always use a fresh private copy above.
JEV_PRODUCT_ALIGNMENT_QUESTIONS: dict[str, Any] = _protocol_questions()
_QUESTION_NAMES = frozenset(_protocol_questions())


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


JEV_PRODUCT_ALIGNMENT_QUESTIONS_SHA256 = _sha256(
    JEV_PRODUCT_ALIGNMENT_QUESTIONS
)
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504, 529}


class ProductEntity(BaseModel):
    """The three fields covered by the confirmed software-product protocol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    manufacturer: str = ""
    price: str = ""

    @field_validator("name", "manufacturer", "price")
    @classmethod
    def normalize_text(cls, value: str, info: Any) -> str:
        normalized = value.strip()
        if info.field_name == "name" and not normalized:
            raise ValueError("name must be nonempty")
        return normalized


class JevProductAdvisorConfig(BaseModel):
    """Connection settings for the pinned, evidence-backed Jev protocol."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    api_key: SecretStr | None = Field(default=None, repr=False)
    api_url: str = JEV_PRODUCT_ALIGNMENT_API_URL
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_attempts: int = Field(default=3, ge=1, le=5)

    @field_validator("api_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith("https://"):
            raise ValueError("api_url must be an HTTPS URL")
        return normalized


class ProductAlignmentProbabilities(BaseModel):
    """Jev's complete typed distribution for the three relationship levels."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    different: float = Field(ge=0, le=1)
    related_or_ambiguous: float = Field(ge=0, le=1)
    same: float = Field(ge=0, le=1)


class JevProductAlignment(BaseModel):
    """Auditable proposal advice for one exact pair of product descriptions."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    schema_version: Literal[1] = 1
    protocol: Literal["jev_product_alignment_v1"] = JEV_PRODUCT_ALIGNMENT_PROTOCOL
    provider: Literal["typesafe_jev"] = "typesafe_jev"
    model: Literal["jev-1.13.0"] = JEV_PRODUCT_ALIGNMENT_MODEL
    questions_sha256: str = JEV_PRODUCT_ALIGNMENT_QUESTIONS_SHA256
    configuration_sha256: str
    left_sha256: str
    right_sha256: str
    request_sha256: str
    score: float = Field(ge=0, le=2)
    confidence: float = Field(ge=0, le=1)
    probabilities: ProductAlignmentProbabilities
    same_name_probability: float = Field(ge=0, le=1)
    same_manufacturer_probability: float = Field(ge=0, le=1)
    compatible_price_probability: float = Field(ge=0, le=1)
    proposal_threshold: float = JEV_PRODUCT_ALIGNMENT_THRESHOLD
    proposal_recommended: bool
    automatic_merge_authorized: Literal[False] = False
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    attempts: int = Field(ge=1)
    elapsed_seconds: float = Field(ge=0)
    assessment_sha256: str


class JevProductProposal(BaseModel):
    """A Jev assessment and its optional durable unverified graph proposal."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    assessment: JevProductAlignment
    proposal_published: bool
    proposal_applied: bool
    proposal_operation_id: str | None = None
    proposal_edge_id: str | None = None
    automatic_merge_authorized: Literal[False] = False


class JevProductAdvisorError(RuntimeError):
    """The Jev service failed or returned an invalid pinned-protocol response."""


def _configuration_sha256(config: JevProductAdvisorConfig) -> str:
    return _sha256(config.model_dump(mode="json", exclude={"api_key"}))


def _credential(config: JevProductAdvisorConfig) -> str:
    value: str | None = None
    if config.api_key is not None:
        value = config.api_key.get_secret_value().strip()
        if value:
            return value
    value = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    if not value:
        with suppress(Exception):
            from dotenv import dotenv_values

            local = dotenv_values(Path.cwd() / ".env")
            value = local.get("JEV_API_KEY") or local.get("TYPESAFE_API_KEY")
    if not isinstance(value, str) or not value.strip():
        raise JevProductAdvisorError(
            "Jev product alignment requires JEV_API_KEY, TYPESAFE_API_KEY, "
            "or JevProductAdvisorConfig(api_key=...)"
        )
    return value.strip()


def _probability(value: Any, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise JevProductAdvisorError(f"Jev returned an invalid {field} probability")
    return float(value)


def _score(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 2
    ):
        raise JevProductAdvisorError("Jev returned an invalid link-state score")
    return float(value)


def _validated_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("model") != JEV_PRODUCT_ALIGNMENT_MODEL:
        raise JevProductAdvisorError("Jev returned a different model")
    answers = value.get("answers")
    if not isinstance(answers, dict) or set(answers) != _QUESTION_NAMES:
        raise JevProductAdvisorError("Jev returned an invalid answer set")
    link = answers["link_state"]
    if not isinstance(link, dict) or link.get("type") != "score":
        raise JevProductAdvisorError("Jev returned an invalid link-state answer")
    score = link.get("score")
    confidence = link.get("confidence")
    legend = link.get("legend")
    probabilities = link.get("probabilities")
    _score(score)
    _probability(confidence, "link_state confidence")
    if (
        not isinstance(legend, dict)
        or tuple(legend.get(str(index)) for index in range(3))
        != JEV_PRODUCT_ALIGNMENT_LEVELS
        or not isinstance(probabilities, dict)
        or set(probabilities) != {"0", "1", "2"}
    ):
        raise JevProductAdvisorError("Jev returned an invalid link-state distribution")
    distribution = [
        _probability(probabilities[str(index)], f"link_state[{index}]")
        for index in range(3)
    ]
    if not math.isclose(sum(distribution), 1.0, abs_tol=0.02):
        raise JevProductAdvisorError("Jev link-state probabilities do not sum to one")
    for name in ("same_name", "same_manufacturer", "compatible_price"):
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            raise JevProductAdvisorError(f"Jev returned an invalid {name} answer")
        _probability(answer.get("noul"), name)
    usage = value.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(key)) is not int or usage[key] < 0
        for key in ("input_tokens", "output_tokens")
    ):
        raise JevProductAdvisorError("Jev returned invalid token usage")
    return value


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        header = response.headers.get("retry-after")
        if header is not None:
            with suppress(ValueError):
                return min(5.0, max(0.0, float(header)))
    return min(2.0, 0.25 * (2 ** (attempt - 1)))


class JevProductAdvisor:
    """Reusable async client for the confirmed proposal-only product protocol."""

    def __init__(
        self,
        config: JevProductAdvisorConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = (config or JevProductAdvisorConfig()).model_copy(deep=True)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                transport=self._transport,
            )
        return self._client

    async def __aenter__(self) -> JevProductAdvisor:
        self._ensure_client()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def compare(
        self,
        left: ProductEntity | dict[str, str],
        right: ProductEntity | dict[str, str],
    ) -> JevProductAlignment:
        """Compare one pair and return proposal advice without mutating memory."""
        left_entity = ProductEntity.model_validate(left)
        right_entity = ProductEntity.model_validate(right)
        state = {
            "entity_a": left_entity.model_dump(),
            "entity_b": right_entity.model_dump(),
        }
        body = {
            "model": JEV_PRODUCT_ALIGNMENT_MODEL,
            "state": state,
            "questions": _protocol_questions(),
        }
        key = _credential(self.config)
        started = time.perf_counter()
        response_value: dict[str, Any] | None = None
        attempts = 0
        last_error: Exception | None = None
        for attempts in range(1, self.config.max_attempts + 1):
            response: httpx.Response | None = None
            try:
                response = await self._ensure_client().post(
                    self.config.api_url,
                    json=body,
                    headers={"Authorization": f"Bearer {key}"},
                )
                if (
                    response.status_code in _RETRYABLE_STATUSES
                    and attempts < self.config.max_attempts
                ):
                    await asyncio.sleep(_retry_delay(response, attempts))
                    continue
                response.raise_for_status()
                response_value = _validated_response(response.json())
                break
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempts >= self.config.max_attempts:
                    break
                await asyncio.sleep(_retry_delay(response, attempts))
            except JevProductAdvisorError:
                raise
            except (httpx.HTTPStatusError, ValueError, TypeError) as exc:
                raise JevProductAdvisorError(
                    "Jev product alignment request failed"
                ) from exc
        if response_value is None:
            raise JevProductAdvisorError(
                "Jev product alignment exhausted transport retries"
            ) from last_error

        answers = response_value["answers"]
        link = answers["link_state"]
        raw_probabilities = link["probabilities"]
        probabilities = ProductAlignmentProbabilities(
            different=float(raw_probabilities["0"]),
            related_or_ambiguous=float(raw_probabilities["1"]),
            same=float(raw_probabilities["2"]),
        )
        usage = response_value["usage"]
        configuration_sha = _configuration_sha256(self.config)
        request_sha = _sha256(
            {
                "configuration_sha256": configuration_sha,
                "model": JEV_PRODUCT_ALIGNMENT_MODEL,
                "questions_sha256": JEV_PRODUCT_ALIGNMENT_QUESTIONS_SHA256,
                "state": state,
            }
        )
        result_identity = {
            "compatible_price_probability": answers["compatible_price"]["noul"],
            "confidence": link["confidence"],
            "probabilities": probabilities.model_dump(),
            "proposal_recommended": link["score"]
            >= JEV_PRODUCT_ALIGNMENT_THRESHOLD,
            "protocol": JEV_PRODUCT_ALIGNMENT_PROTOCOL,
            "request_sha256": request_sha,
            "same_manufacturer_probability": answers["same_manufacturer"]["noul"],
            "same_name_probability": answers["same_name"]["noul"],
            "score": link["score"],
        }
        return JevProductAlignment(
            left_sha256=_sha256(left_entity.model_dump()),
            right_sha256=_sha256(right_entity.model_dump()),
            request_sha256=request_sha,
            configuration_sha256=configuration_sha,
            score=float(link["score"]),
            confidence=float(link["confidence"]),
            probabilities=probabilities,
            same_name_probability=float(answers["same_name"]["noul"]),
            same_manufacturer_probability=float(
                answers["same_manufacturer"]["noul"]
            ),
            compatible_price_probability=float(
                answers["compatible_price"]["noul"]
            ),
            proposal_recommended=(
                link["score"] >= JEV_PRODUCT_ALIGNMENT_THRESHOLD
            ),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            attempts=attempts,
            elapsed_seconds=round(time.perf_counter() - started, 6),
            assessment_sha256=_sha256(result_identity),
        )


async def advise_product_alignment(
    left: ProductEntity | dict[str, str],
    right: ProductEntity | dict[str, str],
    *,
    config: JevProductAdvisorConfig | None = None,
) -> JevProductAlignment:
    """One-shot convenience wrapper for :class:`JevProductAdvisor`."""
    async with JevProductAdvisor(config) as advisor:
        return await advisor.compare(left, right)


def _bound_product_node(
    node: "MemoryNode | None", product: ProductEntity, *, node_id: str
) -> "MemoryNode":
    if (
        node is None
        or node.node_type != NodeType.ENTITY
        or (node.metadata or {}).get("entity_type") != "product"
        or node.content.strip() != product.name
    ):
        raise ValueError(f"Product entity node {node_id} is unavailable")
    return node


async def propose_product_alignment(
    engine: "MemoryEngine",
    left_node_id: str,
    right_node_id: str,
    left: ProductEntity | dict[str, str],
    right: ProductEntity | dict[str, str],
    *,
    user_id: str,
    config: JevProductAdvisorConfig | None = None,
    advisor: JevProductAdvisor | None = None,
) -> JevProductProposal:
    """Assess two product entities and durably publish positive advice.

    The external request runs before the graph transaction. Publication then
    revalidates the exact assessed node snapshots under the backend lock and
    writes one unverified ``RELATES_TO`` edge with a checksummed version-2
    journal record. It never merges, supersedes, or retires either entity.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("Product alignment requires an owner")
    if advisor is not None and config is not None:
        raise ValueError("Pass either advisor or config, not both")

    left_id = str(UUID(left_node_id))
    right_id = str(UUID(right_node_id))
    if left_id == right_id:
        raise ValueError("Product alignment requires two distinct nodes")
    left_product = ProductEntity.model_validate(left)
    right_product = ProductEntity.model_validate(right)
    left_node, right_node = await asyncio.gather(
        engine.get_node(left_id, user_id=user_id),
        engine.get_node(right_id, user_id=user_id),
    )
    left_node = _bound_product_node(left_node, left_product, node_id=left_id)
    right_node = _bound_product_node(right_node, right_product, node_id=right_id)
    if not alias_pair_allowed(left_node, right_node):
        raise ValueError("Product entity nodes are not compatible alias candidates")

    nodes = {left_id: left_node, right_id: right_node}
    ordered_ids = sorted(nodes)

    if advisor is None:
        async with JevProductAdvisor(config) as owned_advisor:
            assessment = await owned_advisor.compare(left_product, right_product)
    else:
        assessment = await advisor.compare(left_product, right_product)

    if not assessment.proposal_recommended:
        return JevProductProposal(
            assessment=assessment,
            proposal_published=False,
            proposal_applied=False,
        )

    payload = {
        "node_bindings": [
            {
                "node_id": left_id,
                "product": left_product.model_dump(mode="json"),
            },
            {
                "node_id": right_id,
                "product": right_product.model_dump(mode="json"),
            },
        ],
        "assessment": assessment.model_dump(mode="json"),
    }
    evidence = AliasProposalEvidence(
        provider=assessment.provider,
        protocol=assessment.protocol,
        model=assessment.model,
        request_sha256=assessment.request_sha256,
        assessment_sha256=assessment.assessment_sha256,
        left_node_sha256=node_checksum(nodes[ordered_ids[0]]),
        right_node_sha256=node_checksum(nodes[ordered_ids[1]]),
        payload_sha256=canonical_hash(payload),
        payload=payload,
    )
    result = await engine._graph_store.propose_alias(
        left_id,
        right_id,
        user_id=user_id,
        alias_type="semantic",
        score=assessment.probabilities.same,
        evidence=evidence,
    )
    if result is None or result.evidence is None or result.operation_id is None:
        raise JevProductAdvisorError(
            "Product entities changed or became unavailable before proposal publication"
        )
    durable_assessment = JevProductAlignment.model_validate(
        result.evidence.payload["assessment"]
    )
    return JevProductProposal(
        assessment=durable_assessment,
        proposal_published=True,
        proposal_applied=result.applied,
        proposal_operation_id=result.operation_id,
        proposal_edge_id=result.edge_id,
    )


__all__ = [
    "JEV_PRODUCT_ALIGNMENT_API_URL",
    "JEV_PRODUCT_ALIGNMENT_MODEL",
    "JEV_PRODUCT_ALIGNMENT_PROTOCOL",
    "JEV_PRODUCT_ALIGNMENT_QUESTIONS",
    "JEV_PRODUCT_ALIGNMENT_QUESTIONS_SHA256",
    "JEV_PRODUCT_ALIGNMENT_THRESHOLD",
    "JevProductAdvisor",
    "JevProductAdvisorConfig",
    "JevProductAdvisorError",
    "JevProductAlignment",
    "JevProductProposal",
    "ProductAlignmentProbabilities",
    "ProductEntity",
    "advise_product_alignment",
    "propose_product_alignment",
]
