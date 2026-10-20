"""Pinned, offline local recognizers behind a complete-result supervisor.

Only offsets cross the worker boundary. A killed/erroring worker cannot return a
partial result. Model loading is completed before any caller content is sent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from importlib import metadata
import json
import multiprocessing
from multiprocessing.connection import Connection
import sys
import time
from typing import Any

from prme.privacy.policy import PrivacyDenied
from prme.privacy.recognizers import LOCAL_ENTITIES, MAX_INPUT_BYTES, Span

VERSIONS = ("presidio-analyzer:2.2.364", "spacy:3.8.11", "en_core_web_sm:3.8.0")
MODEL_FILES_SHA256 = "1bee61d0eb9b94081dfcd894b3f4b7d93867a5d1d6339130bdf964a0850d2274"


class RecognizerIncomplete(Exception):
    """Deliberately not TimeoutError: Presidio must not swallow this failure."""


class CompletePattern:
    """Propagate early regex failures during both finditer creation and iteration."""

    def __init__(self, compiled: Any):
        self._compiled = compiled

    def finditer(self, text: str, *, timeout: float):
        try:
            yield from self._compiled.finditer(text, timeout=min(timeout, 2.0))
        except TimeoutError:
            raise RecognizerIncomplete("privacy_capability_unavailable") from None


def verify_artifacts() -> None:
    if sys.version_info >= (3, 15):
        raise RecognizerIncomplete("privacy_capability_unavailable")
    for name, version in (("presidio-analyzer", "2.2.364"), ("spacy", "3.8.11"),
                          ("en-core-web-sm", "3.8.0")):
        if metadata.version(name) != version:
            raise RecognizerIncomplete("privacy_capability_unavailable")
    distribution = metadata.distribution("en-core-web-sm")
    files = [
        (str(path), hashlib.sha256(path.locate().read_bytes()).hexdigest())
        for path in sorted(distribution.files or (), key=str)
        if str(path).startswith("en_core_web_sm/") and path.hash
    ]
    digest = hashlib.sha256(json.dumps(files, separators=(",", ":")).encode()).hexdigest()
    if digest != MODEL_FILES_SHA256:
        raise RecognizerIncomplete("privacy_capability_unavailable")


def build_engine():
    """No provider/default registry, model download, suffix fetch or disk cache."""
    import copy

    import regex
    import spacy
    from presidio_analyzer import AnalyzerEngine, PatternRecognizer
    from presidio_analyzer.context_aware_enhancers import ContextAwareEnhancer
    from presidio_analyzer.nlp_engine import NerModelConfiguration, SpacyNlpEngine
    from presidio_analyzer.predefined_recognizers import (
        CreditCardRecognizer, EmailRecognizer, IpRecognizer, PhoneRecognizer,
        SpacyRecognizer, UsSsnRecognizer,
    )
    from presidio_analyzer.recognizer_registry import RecognizerRegistry
    from tldextract import TLDExtract

    verify_artifacts()
    suffixes = TLDExtract(cache_dir=None, suffix_list_urls=(), fallback_to_snapshot=True,
                          include_psl_private_domains=False)

    class OfflineEmail(EmailRecognizer):
        def validate_result(self, pattern_text):
            return suffixes(pattern_text).fqdn != ""

    class NoContext(ContextAwareEnhancer):
        def enhance_using_context(self, text, raw_results, nlp_artifacts, recognizers, context=None):
            return raw_results

    model = spacy.load("en_core_web_sm")
    mapping = {"PERSON": "PERSON", "GPE": "LOCATION", "LOC": "LOCATION", "FAC": "LOCATION"}
    ignored = set(model.get_pipe("ner").labels) - mapping.keys()
    nlp = SpacyNlpEngine(
        models=[{"lang_code": "en", "model_name": "en_core_web_sm"}],
        ner_model_configuration=NerModelConfiguration(
            model_to_presidio_entity_mapping=mapping, labels_to_ignore=sorted(ignored),
            default_score=0.85, low_score_entity_names=set(),
        ),
    )
    # Explicit already-loaded engine prevents SpacyNlpEngine.load/download.
    nlp.nlp = {"en": model}
    recognizers = [
        OfflineEmail(), PhoneRecognizer(supported_regions=("US", "GB"), leniency=1),
        SpacyRecognizer(supported_entities=["PERSON", "LOCATION"]),
        UsSsnRecognizer(), CreditCardRecognizer(), IpRecognizer(),
    ]
    patterns = []
    for recognizer in recognizers:
        if isinstance(recognizer, PatternRecognizer):
            recognizer.patterns = copy.deepcopy(recognizer.patterns)
            for pattern in recognizer.patterns:
                pattern.compiled_with_flags = recognizer.global_regex_flags
                pattern.compiled_regex = CompletePattern(regex.compile(pattern.regex, flags=pattern.compiled_with_flags))
                patterns.append((pattern, pattern.compiled_regex, pattern.compiled_with_flags))
    engine = AnalyzerEngine(
        registry=RecognizerRegistry(recognizers=recognizers, supported_languages=["en"]),
        nlp_engine=nlp, supported_languages=["en"], log_decision_process=False,
        context_aware_enhancer=NoContext(0, 0, 0, 0),
    )
    # The fixed entity set selects every declared recognizer exactly once.
    expected = {id(recognizer) for recognizer in recognizers}
    invoked = set()
    for recognizer in recognizers:
        original = recognizer.analyze
        identity = id(recognizer)

        def complete(*args, _original=original, _identity=identity, **kwargs):
            result = _original(*args, **kwargs)
            invoked.add(_identity)
            return result

        recognizer.analyze = complete

    def analyze(text: str) -> tuple[Span, ...]:
        invoked.clear()
        if set(engine.get_supported_entities("en")) != set(LOCAL_ENTITIES):
            raise RecognizerIncomplete("privacy_capability_unavailable")
        results = engine.analyze(text, language="en", entities=list(LOCAL_ENTITIES),
                                 score_threshold=0, return_decision_process=False)
        if invoked != expected or any(pattern.compiled_regex is not wrapper or pattern.compiled_with_flags != flags
                                      for pattern, wrapper, flags in patterns):
            raise RecognizerIncomplete("privacy_capability_unavailable")
        spans = []
        for result in results:
            category, threshold = LOCAL_ENTITIES[result.entity_type]
            if result.score >= threshold:
                span = Span(category, result.start, result.end, "presidio-local-v1")
                span.validate(text)
                spans.append(span)
        return tuple(spans)

    return analyze


def _worker(connection: Connection) -> None:
    import logging
    import os
    import socket
    import warnings

    # These changes are confined to this dedicated child. No content-bearing
    # third-party warning/trace may escape before the admission decision.
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore")
    sys.stderr = open(os.devnull, "w")

    def offline(*_args, **_kwargs):
        raise RecognizerIncomplete("privacy_capability_unavailable")

    socket.create_connection = offline
    socket.socket.connect = offline
    socket.socket.connect_ex = offline
    try:
        analyze = build_engine()
        connection.send(("ready", VERSIONS))
        while True:
            request = connection.recv()
            if request is None:
                return
            if (not isinstance(request, tuple) or any(not isinstance(text, str) for text in request)
                    or sum(len(text.encode("utf-8")) for text in request) > MAX_INPUT_BYTES):
                raise RecognizerIncomplete("privacy_capability_unavailable")
            result = tuple(analyze(text) if text else () for text in request)
            connection.send(("complete", result))
    except (EOFError, BrokenPipeError):
        return
    except Exception:
        try:
            connection.send(("denied", None))
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()


@dataclass(frozen=True, repr=False)
class Detection:
    spans: tuple[tuple[Span, ...], ...]
    versions: tuple[str, ...] = VERSIONS
    complete: bool = True


class LocalAnalyzer:
    """One bounded local worker. The owner must close it when shutting down."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._process: Any = None
        self._connection: Connection | None = None

    async def _receive(self, seconds: float):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._connection is None or self._process is None:
                raise PrivacyDenied("privacy_capability_unavailable")
            if self._connection.poll():
                return self._connection.recv()
            if not self._process.is_alive():
                break
            await asyncio.sleep(min(0.005, max(0, deadline - time.monotonic())))
        raise PrivacyDenied("privacy_capability_unavailable")

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._process is not None:
            if self._process.is_alive():
                self._process.kill()
            self._process.join(timeout=1)
            self._process.close()
            self._process = None

    async def ready(self) -> None:
        async with self._lock:
            if self._process is not None and self._process.is_alive():
                return
            self.close()
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe()
            self._connection = parent
            self._process = context.Process(target=_worker, args=(child,), daemon=True)
            try:
                self._process.start()
                child.close()
                if await self._receive(30) != ("ready", VERSIONS):
                    raise PrivacyDenied("privacy_capability_unavailable")
            except BaseException:
                child.close()
                self.close()
                raise PrivacyDenied("privacy_capability_unavailable") from None

    async def analyze(self, texts: tuple[str, ...], *, seconds: float) -> Detection:
        # Initialization sees no caller content and does not spend scan time.
        await self.ready()
        async with self._lock:
            try:
                if seconds <= 0 or self._connection is None:
                    raise PrivacyDenied("privacy_capability_unavailable")
                self._connection.send(texts)
                status, spans = await self._receive(seconds)
                if status != "complete" or not isinstance(spans, tuple) or len(spans) != len(texts):
                    raise PrivacyDenied("privacy_capability_unavailable")
                for text, results in zip(texts, spans, strict=True):
                    if not isinstance(results, tuple):
                        raise PrivacyDenied("privacy_invalid_result")
                    for result in results:
                        if type(result) is not Span:
                            raise PrivacyDenied("privacy_invalid_result")
                        result.validate(text)
                return Detection(spans)
            except BaseException:
                self.close()
                raise PrivacyDenied("privacy_capability_unavailable") from None
