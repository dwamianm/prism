**SCIENTIFIC AUDIT REPORT**

Relational Memory Substrate (RMS) & Portable Relational Memory Engine
(PRME)

RFC-0001 through RFC-0015 --- Full Evaluation

  ---------------------------- ------------------------------------------
  **Reviewer Role**            Independent Scientist, AI Memory Systems

  **Date**                     February 19, 2026

  **Status**                   DRAFT --- For Scientific Review

  **Scope**                    Hypothesis Validity + RFC-0001 through
                               RFC-0015
  ---------------------------- ------------------------------------------

**Table of Contents**

**1. Executive Summary**

This report presents an independent, unvarnished scientific audit of the
hypothesis "Memory is the core of intelligence" and its associated
Relational Memory Substrate (RMS) and Portable Relational Memory Engine
(PRME) RFC series (RFC-0001 through RFC-0015).

The audit was conducted from the perspective of an experienced
researcher in AI memory systems, cognitive architecture, and distributed
systems engineering. No effort was made to flatter the author. Where the
work is strong, that is stated plainly. Where it fails or is incomplete,
that too is stated plainly.

**1.1 Hypothesis Verdict**

+-----------------------------------------------------------------------+
| **Hypothesis: \"Memory is the core of intelligence\"**                |
|                                                                       |
| Verdict: PARTIALLY CORRECT --- A valid and useful engineering claim,  |
| but philosophically and scientifically overstated. The hypothesis     |
| holds strong pragmatic utility for LLM system design but makes        |
| epistemic overclaims when applied to general intelligence.            |
+-----------------------------------------------------------------------+

**1.2 RFC Suite Overall Verdict**

The RFC suite is technically ambitious and architecturally coherent. It
addresses real, well-documented failure modes in current LLM memory
systems. The foundational RFCs (0001, 0002, 0003, 0004, 0005, 0006,
0007, 0008, 0010) form a serious and defensible engineering framework.

However, the suite suffers from: (1) an absence of empirical validation
or benchmarks; (2) underestimation of implementation complexity,
particularly around determinism at scale; (3) the later RFCs
(0012--0015) venturing into territory that is speculative and, in some
cases, dangerously underspecified for the risks involved.

The work is promising enough to merit continued development, but several
claims need to be scaled back, and the evaluation framework urgently
needs real-world data before these RFCs progress beyond Draft status.

**2. Hypothesis Evaluation: \"Memory is the Core of Intelligence\"**

**2.1 The Claim**

The hypothesis posits that memory is not merely a component of
intelligence but its core --- that lived experience, emotion, and
cognition fundamentally depend on it. The author implies this justifies
a rigorous, persistent, relational memory system for LLMs.

**2.2 What the Evidence Supports**

The hypothesis draws strength from several well-established findings:

-   Memory is necessary for intelligence: Without any form of persistent
    state, adaptive behavior is impossible. This is uncontroversial from
    Turing forward.

-   Episodic and semantic memory are functionally separable in
    biological systems (Tulving, 1972), and conflating them causes
    retrieval failures --- exactly the problem the RFC suite targets.

-   Working memory capacity correlates strongly with general fluid
    intelligence (Kyllonen & Christal, 1990; Conway et al., 2003).

-   LLMs without persistent memory demonstrably fail at tasks requiring
    multi-session coherence. This is the most direct empirical
    justification for the work.

-   Continual learning and catastrophic forgetting research confirms
    that naive storage without selective reinforcement and decay
    degrades performance --- directly motivating RFC-0003.

**2.3 Where the Claim Overreaches**

The hypothesis as stated makes a stronger claim than the evidence
warrants. The following are significant problems:

-   \"Core\" is doing too much work. Memory is necessary but not
    sufficient for intelligence. Attention, reasoning, action selection,
    and goal representation are not reducible to memory alone. Baars'
    Global Workspace Theory, for example, places attention and
    broadcasting at the center, not memory per se.

-   The claim conflates different memory types. Procedural memory
    (skills), declarative memory (facts), episodic memory (events), and
    working memory operate through partially distinct neural and
    computational mechanisms. A single relational substrate may not map
    cleanly onto all of them.

-   Current LLMs do not have emotion in any functionally meaningful
    sense. RFC-0013 (Emotional and Behavioral Signal Tracking)
    implicitly accepts this conflation and attempts to operationalize
    \"emotional signals\" from text, which is a proxy, not the thing
    itself.

-   Cognition without memory exists: reflex arcs, some forms of implicit
    learning, and reactive systems operate without episodic or semantic
    memory. The hypothesis would classify these as non-intelligent,
    which is philosophically contestable.

**2.4 Pragmatic Reframing (Recommended)**

A stronger, more defensible formulation would be:

+-----------------------------------------------------------------------+
| **Recommended Restatement**                                           |
|                                                                       |
| \"Persistent, structured, and retrievable memory is a necessary       |
| precondition for long-horizon coherent behavior in LLM-based systems. |
| Without it, these systems cannot accumulate context, maintain         |
| consistency, or generalize across sessions.\"                         |
+-----------------------------------------------------------------------+

This version is empirically defensible, architecturally actionable, and
does not make overclaims about the nature of biological intelligence.
The RFCs should be re-framed around this more modest but still
compelling version.

**2.5 Hypothesis Score**

**Verdict: PARTIALLY VALID --- Strong engineering motivation, weak as a
general theory of intelligence. The RFCs are best justified on pragmatic
engineering grounds, not on the grand claim.**

**3. RFC Ratings Summary**

  ------------------------------------------------------------------------------
  **RFC**    **Title**                    **Rating**   **Key Finding**
  ---------- ---------------------------- ------------ -------------------------
  RFC-0001   PRME Technical Spec          STRONG       Solid architecture. The
                                                       right foundation.

  RFC-0002   Git Sync Profile             SOLID        Pragmatic. Merge
                                                       semantics need hardening.

  RFC-0003   Forgetting & Decay Model     STRONG       Best RFC in the suite.
                                                       Well-grounded.

  RFC-0004   Namespace & Scope Isolation  SOLID        Correct and necessary.
                                                       Access control gaps.

  RFC-0005   Epistemic State Model        STRONG       Genuinely novel for LLM
                                                       memory. Rigorous.

  RFC-0006   Confidence Evolution         SOLID        Good mechanics. Trust
                                                       calibration circular.

  RFC-0007   Retrieval Cost & Token       SOLID        Practical. STR formula
             Efficiency                                needs validation.

  RFC-0008   Memory Usage Feedback Loop   SOLID        Right direction. Signal
                                                       detection underspecified.

  RFC-0009   Temporal Pattern Modeling    PARTIAL      Useful but prediction
                                                       claims are premature.

  RFC-0010   State vs Event Separation    STRONG       Architectural
                                                       cornerstone.
                                                       Well-specified.

  RFC-0011   Multi-Agent Memory Semantics PARTIAL      Necessary but trust model
                                                       is naive.

  RFC-0012   Intent Memory Model          PARTIAL      Ambitious. Extraction
                                                       reliability unproven.

  RFC-0013   Emotional & Behavioral       WEAK         Dangerous if misused.
             Signals                                   Proxy ≠ emotion.

  RFC-0014   Relevance Drift Model        PARTIAL      Concept valid. Embedding
                                                       drift measurement
                                                       fragile.

  RFC-0015   Memory Branching &           PARTIAL      Interesting. Merge
             Simulation                                conflict resolution
                                                       underspecified.
  ------------------------------------------------------------------------------

Ratings: STRONG = Production-ready direction \| SOLID = Defensible,
needs validation \| PARTIAL = Concept valid, significant gaps \| WEAK =
Requires fundamental rethinking

**4. Individual RFC Audits**

**RFC-0001 --- Portable Relational Memory Engine (PRME)**

**Rating: STRONG**

**4.1.1 What Works**

The PRME specification gets the core architecture right. The combination
of an append-only event log, a graph-based relational model, hybrid
retrieval (graph + vector + lexical), and scheduled reorganization is
well-reasoned and aligns with how production-grade event-sourced systems
are built.

The decision to use DuckDB for the event store and Kùzu for the graph
layer is pragmatic. Both are embedded, high-performance, and capable of
the workloads described. The HNSW vector index choice is
industry-standard. This is not cargo-culting --- these are the right
tools.

Deterministic rebuild from event logs is a key design insight. Most
memory systems treat derived state as authoritative; PRME correctly
identifies this as a source of brittleness.

**4.1.2 Critical Gaps**

-   The re-ranking formula (w1•semantic + w2•lexical + w3•graph +
    w4•recency + w5•salience + w6•confidence) is presented without any
    empirical basis for the weights. These weights are the most
    consequential design decision in the retrieval layer, and
    \"configurable\" is not a substitute for guidance derived from
    experiments.

-   The system is described as \"self-organizing\" but there is no
    specification of how the organizer decides what to do. The scheduled
    jobs are listed (salience recalculation, promotion, deduplication)
    but the decision logic is not specified. Without this, two compliant
    implementations will behave entirely differently.

-   The evaluation criteria in Section 7 are stated as pass/fail, not as
    measurable benchmarks. \"Recalls long-term stable preferences\" is
    not a test --- it is a wish. What retrieval accuracy, at what
    latency, measured how?

-   No discussion of cold start behavior. A new memory system with no
    history will have no graph structure, no reinforced salience, and no
    patterns. How does it degrade gracefully?

**Verdict: STRONG --- Right architecture. Desperately needs benchmarks
and decision logic for the organizer.**

**RFC-0002 --- RMS Git Sync Profile**

**Rating: SOLID**

**4.2.1 What Works**

Using Git as a transport layer is genuinely clever. It gives you
versioning, provenance, branching, and a well-understood merge substrate
for free. The append-only ops file design is sound --- it maps directly
to the event sourcing model and avoids the classic Git conflict problem
by treating all writes as additive.

The ops format (JSONL + Zstandard) is a good choice: readable,
efficient, and widely supported. The decision to separate sync from
storage (the RFC explicitly does not mandate a specific database) is
architecturally correct.

**4.2.2 Critical Gaps**

-   The merge semantics are punted. The RFC says conflicting assertions
    must be resolved at \"the memory semantic layer\" but provides zero
    detail on how this actually works when two devices diverge for weeks
    and then sync. This is the hardest problem in distributed memory and
    the RFC simply defers it.

-   Git LFS for attachments is mentioned but the interaction with the
    ops log is not specified. If an embedding vector referenced in an
    ops file is not present because LFS sync failed, what happens? How
    are partial syncs handled?

-   No specification of what happens when the same entity is
    independently created on two devices with different UUIDs.
    Idempotency is declared but the deduplication semantics are not.

-   Git is not a real-time protocol. For use cases requiring low-latency
    memory synchronization (e.g., a multi-device assistant
    mid-conversation), this transport has unacceptable latency. The RFC
    should explicitly scope itself to async/background sync.

**Verdict: SOLID --- Good transport design. Merge conflict handling is
the elephant in the room.**

**RFC-0003 --- Forgetting and Decay Model**

**Rating: STRONG (Best RFC in the suite)**

**4.3.1 What Works**

This is the most rigorous RFC in the set. It is grounded in
well-understood mathematical models (exponential decay, half-life
functions), provides multiple configurable profiles, correctly separates
decay from reinforcement, and defines deterministic tier transitions.

The reinforcement decay (Section 7.3) --- where reinforcement itself
decays if not refreshed --- is a genuine insight that prevents \"once
hot, forever hot\" pathology. This mirrors spacing effect research
(Ebbinghaus, 1885) and is directly applicable.

The suppression model (Section 9) is also well-conceived: distinguishing
between \"not relevant\" (decay) and \"not allowed\" (suppression) is an
important distinction that most memory systems collapse.

**4.3.2 Critical Gaps**

-   The conformance tests (Section 16) are described at a high level but
    there is no reference implementation or test suite. Claiming
    determinism without a runnable test harness is insufficient for a
    Standards Track document.

-   The interaction between type-specific decay defaults and
    per-namespace policy is not fully specified. If a preference has
    no-decay profile by type but a namespace applies fast decay, which
    wins? Precedence rules are absent.

-   \"Deletion tombstone\" in Section 10.2 is required but the format of
    the tombstone is not specified. This will lead to incompatible
    implementations.

**Verdict: STRONG --- Theoretically sound, practically well-designed.
Needs a reference test harness.**

**RFC-0004 --- Namespace and Scope Isolation**

**Rating: SOLID**

**4.4.1 What Works**

The namespace model is correctly specified as a first-class construct.
The hierarchical namespace design with explicit visibility policies
(PRIVATE, PARENT_VISIBLE, etc.) is sensible and aligns with how
enterprise access control systems are built.

The requirement that similarity search does not bypass namespace filters
(Section 9) is critical and often overlooked in vector DB
implementations. This is not a trivial guarantee to provide when using
HNSW, and the RFC is right to call it out.

**4.4.2 Critical Gaps**

-   The inheritance model for child namespaces is underspecified.
    \"Child namespaces MUST NOT automatically inherit retrieval rights
    from parents unless explicitly configured\" is stated, but the
    configuration mechanism is not defined. How does a user grant
    inheritance? Through what interface?

-   Inference attacks are mentioned (Section 10) but not addressed. In a
    system where similarity search results can be observed, namespace
    existence can be inferred even when content is hidden. Side-channel
    leakage in vector databases is a real threat and deserves more than
    a single MUST.

-   No specification for what happens when a user switches context
    rapidly between namespaces within a single session. Retrieval
    context isolation per query is implied but not formalized.

**Verdict: SOLID --- Necessary and correctly framed. Access control
model needs deeper specification.**

**RFC-0005 --- Epistemic State Model**

**Rating: STRONG**

**4.5.1 What Works**

This is the RFC that most clearly distinguishes RMS from naive vector
store approaches. Treating epistemic type as a first-class attribute ---
distinguishing OBSERVED, ASSERTED, INFERRED, HYPOTHETICAL, CONDITIONAL,
DEPRECATED, and UNVERIFIED --- is philosophically well-grounded and
practically essential.

The approach directly maps to known failure modes: LLMs frequently
confuse model inferences with user assertions, treat hypothetical
statements as facts, and silently overwrite contradictions. This RFC
provides the data model to prevent all of these. That is a meaningful
contribution.

The transition graph (UNVERIFIED → ASSERTED, HYPOTHETICAL → ASSERTED,
etc.) is clean and the requirement that each transition generates a log
entry is correct.

**4.5.2 Critical Gaps**

-   The epistemic classification itself must be performed by some
    extraction pipeline --- but that pipeline is not specified in this
    RFC or anywhere else. Classifying whether a statement is OBSERVED vs
    INFERRED vs HYPOTHETICAL requires an NLP model that will itself make
    errors. The downstream confidence in epistemic labels is not
    modeled.

-   The contradiction modeling (Section 8) says both contradicting
    objects must remain stored and retrieval should prefer
    higher-confidence, non-deprecated objects. But in the case where
    confidence is equal, the tie-breaking rule is missing.

-   CONDITIONAL claims (Section 9) require condition evaluation at
    retrieval time. Who evaluates the condition? The model? A rules
    engine? This is a significant implementation question left entirely
    open.

**Verdict: STRONG --- Principled and necessary. Extraction pipeline
reliability is the unaddressed dependency.**

**RFC-0006 --- Confidence Evolution and Reinforcement Model**

**Rating: SOLID**

**4.6.1 What Works**

The bounded update functions (confidence_new = confidence_old + α \*
(1 - confidence_old) for positive; confidence_new = confidence_old \*
(1 - β) for negative) are mathematically sound. They are asymptotic,
bounded in \[0,1\], and deterministic. The optional Bayesian model
(Section 9) is a sensible alternative.

The saturation controls (Section 10) --- logarithmic scaling,
diminishing returns for identical sources --- prevent the \"echo
chamber\" failure mode where a single high-trust actor can drive
confidence to 1.0 arbitrarily.

**4.6.2 Critical Gaps**

-   The initial confidence baseline values are asserted without
    empirical grounding. Why is OBSERVED (USER) → 0.85 but ASSERTED
    (USER) → 0.70? These numbers are plausible but arbitrary. They will
    have a compounding effect across the entire system and deserve at
    minimum a sensitivity analysis.

-   Cross-agent reinforcement (Section 12) says confidence should
    increase if multiple agents independently assert the same claim. But
    agent independence is not guaranteed or verified. If two agents both
    draw from the same source document, they are not independent. This
    is a classic "naive Bayes" mistake --- treating correlated evidence
    as independent evidence and systematically over-updating confidence.

-   The interaction between confidence decay (Section 11) and the decay
    model in RFC-0003 is described in one sentence: they must remain
    "mathematically separable." This is insufficient. A joint model must
    be specified or the two will diverge in practice.

**Verdict: SOLID --- Sound mechanics. The agent independence assumption
is a significant theoretical vulnerability.**

**RFC-0007 --- Retrieval Cost and Token Efficiency Model**

**Rating: SOLID**

**4.7.1 What Works**

The Signal-to-Token Ratio (STR) concept is directly actionable and
addresses a real problem: systems that optimize only for semantic
relevance will include verbose, low-density content and exhaust context
budgets with noise. STR is a clean framing.

The multi-resolution hierarchy (summary → snapshot → structured fact →
raw event) is well-specified and implementable. The context packing
algorithm in Section 9 is deterministic and the budget-based stopping
criterion is correct.

**4.7.2 Critical Gaps**

-   The STR formula (STR = signal_score / token_cost) treats
    signal_score as given, but signal_score is itself a composite of
    multiple scores from RFC-0001. There is a potential circular
    dependency: context packing depends on signal score, signal score
    depends on salience, salience depends on usage (RFC-0008), and usage
    depends on being included in context.

-   Token cost estimation (Section 6) via character length / constant is
    a very rough approximation that degrades significantly for languages
    with different character-to-token ratios (Chinese, Japanese, Korean)
    and for structured formats (JSON serialization of graph data is much
    less token-efficient than prose).

-   No specification for how the system behaves when context budget is
    exhausted before the most critical memory is packed. Priority
    ordering between entity snapshots, stable facts, and active tasks is
    given but not ranked in the case of budget overflow.

**Verdict: SOLID --- Practical and well-reasoned. STR circular
dependency and multilingual token estimation need resolution.**

**RFC-0008 --- Memory Usage Feedback Loop**

**Rating: SOLID**

**4.8.1 What Works**

The core insight --- that a memory system which does not observe whether
its outputs were useful cannot improve --- is correct and important. The
feedback lifecycle (injection tracking → signal capture →
confidence/salience update) is well-structured.

The distinction between REFERENCED, CONFIRMED, CORRECTED, IRRELEVANT,
and UNUSED signals is useful and finer-grained than most feedback
systems in the literature.

**4.8.2 Critical Gaps**

-   Signal detection is the critical unresolved problem. The RFC says
    signals may be derived from \"response analysis (e.g., reference
    tracing)\" but this requires either LLM self-evaluation or
    fine-grained attribution analysis. These are hard, unsolved problems
    that the RFC treats as implementation details. They are not.

-   The model_response_hash in the Feedback Session (Section 4) implies
    the system can hash and compare model responses. But LLM responses
    are non-deterministic. The same memory injected into two nominally
    identical prompts may produce different responses, making feedback
    correlation unreliable.

-   Feedback saturation controls (Section 11) cap reinforcement per
    session but do not address temporal clustering. If a user has an
    intensive session on one topic and then never returns to it, the
    feedback loop will have over-reinforced that topic relative to its
    long-term importance.

**Verdict: SOLID --- The right idea. Signal detection reliability is
unsolved and understated as a challenge.**

**RFC-0009 --- Temporal Pattern Modeling**

**Rating: PARTIAL**

**4.9.1 What Works**

The motivation is valid: timestamps without temporal structure are a
missed opportunity. Detecting recurring commitments (weekly meetings,
quarterly reviews) and adjusting salience accordingly is directly
actionable.

The temporal salience boost formula and dormancy detection are sensible
extensions of the decay model.

**4.9.2 Critical Gaps**

-   Prediction claims are significant: the RFC says the system can
    predict \"next_predicted_occurrence.\" Pattern detection from
    conversational data is considerably harder than from structured
    calendar data. With sparse, noisy signals and irregular user
    behavior, false pattern detection will be common, and the downstream
    effects of injecting incorrect predictions into context are not
    discussed.

-   The RFC says \"no stochastic modeling is permitted unless
    seed-controlled\" but many pattern detection algorithms (k-means
    clustering, changepoint detection) have stochastic components that
    are difficult to fully seed-control in practice across different
    environments.

-   Burst detection (Section 10) may produce objects that look like
    \"emerging priority\" but are actually anomalies or errors. The
    false positive rate for burst detection on short-horizon
    conversational data is likely high and is not addressed.

**Verdict: PARTIAL --- Incremental temporal awareness is useful.
Prediction and burst detection are oversold.**

**RFC-0010 --- State vs Event Separation Model**

**Rating: STRONG**

**4.10.1 What Works**

This RFC articulates the event sourcing pattern with precision and
applies it correctly to the memory domain. The three-layer separation
(Event Layer, Derived State Layer, Materialized Views) is
architecturally sound and maps cleanly onto established distributed
systems patterns.

The requirement that derived state must never be the merge authority,
and that state is always recomputed from events, is the correct answer
to the distributed consistency problem. This RFC is the theoretical
foundation that makes RFC-0002 (Git Sync) coherent.

**4.10.2 Critical Gaps**

-   Event compaction (Section 10) is allowed \"for storage
    optimization\" but the RFC says it must preserve \"logical
    equivalence.\" Defining logical equivalence precisely is non-trivial
    --- especially when compaction interacts with the epistemic state
    model and confidence evolution. A compacted event log that loses
    intermediate confidence updates may replay to a different final
    state than the full log if policy changes occurred mid-history.

-   The spec requires tamper-evident logs (Section 14) but does not
    mandate Merkle trees or cryptographic chaining. Without these,
    tamper-evidence is declarative, not enforceable.

**Verdict: STRONG --- Architectural cornerstone.
Compaction-with-policy-change interaction needs formal treatment.**

**RFC-0011 --- Multi-Agent Memory Semantics**

**Rating: PARTIAL**

**4.11.1 What Works**

The formal specification of agent identity, authorship attribution, and
trust weighting is a necessary addition as AI systems become more
agentic. The requirement that revoked agents cannot modify future memory
while historical records are preserved is architecturally correct.

**4.11.2 Critical Gaps**

-   The trust score model (\[0.0, 1.0\] per actor) is naive for
    production deployment. Trust is context-dependent: an agent may be
    highly trusted for calendar data but untrusted for medical advice. A
    scalar trust score collapses this into a single dimension that will
    produce wrong results.

-   Agent identity verification is MUST-level, but the RFC does not
    specify the verification mechanism. In a distributed system, how is
    agent identity actually established? Cryptographic signing is listed
    as SHOULD, which is dangerously weak for a security-sensitive
    system.

-   \"Byzantine fault-tolerant consensus models\" are listed as a future
    extension but they are not optional for a deployed multi-agent
    system with adversarial potential. The threat model is
    underestimated.

**Verdict: PARTIAL --- Necessary but the trust model is too simplistic
for real adversarial environments.**

**RFC-0012 --- Intent Memory Model**

**Rating: PARTIAL**

**4.12.1 What Works**

Modeling intent --- goals, commitments, open questions,
decisions-in-progress --- as first-class, structured memory objects is
the right abstraction. Most memory systems treat everything as an
undifferentiated fact store; intent objects are a meaningful upgrade.

The lifecycle state machine (ACTIVE, PENDING, COMPLETED, CANCELLED,
SUPERSEDED, EXPIRED, ON_HOLD) is well-specified and the requirement that
all transitions generate log entries is correct.

**4.12.2 Critical Gaps**

-   Intent extraction from unstructured conversation is the key unsolved
    problem. The RFC says intent may be created via \"deterministic
    extraction pipelines\" but this is circular. Intent extraction from
    natural language is a hard NLP problem with high error rates. If the
    system misclassifies a casual comment as a COMMITMENT, the
    downstream consequences are significant. Error rates and correction
    mechanisms are not addressed.

-   The RISK intent type (Section 12) says risks must not silently decay
    without review, but the review triggering mechanism is
    underspecified. What happens if a HIGH SEVERITY risk is detected and
    the user never reviews it? Does the system escalate? Block? Notify?

-   Dependency modeling (Section 8) between intent objects creates the
    possibility of complex dependency graphs. There is no specification
    for cycle detection or resolution in dependency chains.

**Verdict: PARTIAL --- Good abstraction. Extraction reliability and
dependency cycle handling are critical gaps.**

**RFC-0013 --- Emotional and Behavioral Signal Tracking**

**Rating: WEAK (Proceed with caution)**

**4.13.1 What Works**

The motivation is real: AI systems that are completely affect-blind will
miss important context about user state. The idea of tracking behavioral
signals (response delays, priority shifts, focus changes) as proxies for
engagement is more defensible than tracking emotional signals from text.

The privacy framework (Section 12) and the requirement for explicit
retrieval permission for sensitive signals shows awareness of the risks
involved.

**4.13.2 Critical Problems --- This RFC Requires Major Revision**

-   Text-derived \"emotional signals\" are not emotions. They are
    statistical correlates of linguistic patterns. The RFC conflates
    signal extraction from text with tracking emotional state. This is a
    category error with real-world harm potential.

-   BURNOUT_RISK_LEVEL as a derived state from conversational signals is
    dangerous if acted upon without proper qualification. A system that
    infers BURNOUT_RISK from text and then modifies its behavior (or
    worse, reports this to an employer or manager) without explicit user
    consent is a serious privacy and psychological risk.

-   The extraction determinism requirement (\"stochastic extraction
    without fixed seeds is NOT permitted\") is in direct tension with
    modern NLP models, which are inherently stochastic. Achieving
    deterministic sentiment/emotion extraction from an LLM is not
    currently feasible without significant capability sacrifice.

-   The signal types (STRESS, FRUSTRATION, UNCERTAINTY, DISENGAGEMENT)
    applied to conversational text will have high false positive rates
    in normal professional usage. Someone writing terse messages while
    multitasking is not necessarily stressed. The consequences of acting
    on false positives in an affect-aware system are not addressed.

-   Opt-out is listed as SHOULD (not MUST). For a system tracking
    inferred emotional state, opt-out must be MUST.

+-----------------------------------------------------------------------+
| **Recommendation**                                                    |
|                                                                       |
| Emotional signal tracking should be fundamentally redesigned. In the  |
| current form it is: (1) epistemically dubious (confusing proxies with |
| actual emotional states), (2) privacy-threatening, (3) technically    |
| underspecified for determinism. Behavioral signals (engagement,       |
| activity patterns) may be salvageable with a strict proxy-only        |
| framing and mandatory opt-in.                                         |
+-----------------------------------------------------------------------+

**Verdict: WEAK --- Dangerous as specified. Requires fundamental
rethinking before inclusion in the suite.**

**RFC-0014 --- Relevance Drift Model**

**Rating: PARTIAL**

**4.14.1 What Works**

The concept is sound: content can become irrelevant not because it is
false or old, but because the surrounding context has changed.
Separating drift from decay is the correct architectural decision.

The composite salience formula (salience_effective = salience_decayed \*
(1 - drift_score)) is clean and the inverse relationship between drift
and effective salience is intuitively correct.

**4.14.2 Critical Gaps**

-   Embedding centroid drift measurement is fragile. Embeddings from
    different model versions are not comparable, and even within a model
    version, the embedding space is not stable over long time horizons
    as fine-tuning updates accumulate. The RFC requires embedding_model
    version tracking (correct) but does not specify how to handle drift
    scores computed under a previous embedding model when the model
    changes.

-   The "rolling centroid of recent high-salience objects" that defines
    the reference point for drift is itself a derived metric that can be
    manipulated. If an adversary or malfunctioning agent floods the
    system with high-salience objects on a specific topic, the centroid
    will shift and cause legitimate memories to appear drifted.

-   Drift reversal (Section 12) says re-alignment must be "gradual and
    deterministic" but the rate of reversal is not specified. A memory
    that was drifted for six months and then becomes suddenly relevant
    again should recover faster than a memory that slowly drifted back
    into relevance. The model does not support this.

**Verdict: PARTIAL --- Valid concept, implementation fragility under
embedding model changes is a structural problem.**

**RFC-0015 --- Memory Branching and Simulation Model**

**Rating: PARTIAL**

**4.15.1 What Works**

The motivation is correct: hypothetical exploration should not
contaminate canonical memory. The branch type taxonomy (CANONICAL,
SIMULATION, COUNTERFACTUAL, SANDBOX, PLANNING, TEST) is sensible, and
the requirement that branches maintain separate event logs with
isolation from canonical is the right answer.

The Git analogy is coherent given RFC-0002, and multi-level branching
with parent-child relationships is specified with appropriate
constraints (no cyclic ancestry).

**4.15.2 Critical Gaps**

-   Merge conflict resolution (Section 9) is the hardest problem in
    distributed systems and the RFC reduces it to: \"preserve both
    claims in history, apply trust and confidence weighting, transition
    superseded objects appropriately.\" This is a description of desired
    outcomes, not a specification. Two implementations following these
    rules will behave differently.

-   The resource cost of maintaining multiple parallel branch event logs
    is not discussed. If an LLM assistant spins up simulation branches
    for every planning exercise, storage and compute costs can compound
    rapidly. Branching must be cost-bounded.

-   There is no specification for what happens when a simulation branch
    references a canonical object that is later superseded in the
    canonical branch. Does the simulation branch see the old version or
    the new version? This is a fundamental consistency question for
    temporal isolation.

-   Expiration and cleanup (Section 13) specifies auto_delete_policy but
    gives no guidance on what a reasonable policy looks like. Without
    defaults, users will either never clean up branches or delete them
    too aggressively.

**Verdict: PARTIAL --- Right idea, merge resolution is underspecified,
and resource cost is ignored.**

**5. Cross-Cutting Concerns**

**5.1 The Determinism Problem**

The word "deterministic" appears throughout the RFC suite as a hard
requirement. This commitment is admirable in principle but is
significantly more difficult than the RFCs acknowledge.

-   Determinism across LLM inference calls is not achievable with
    current production systems (temperature \> 0, different GPU
    hardware, different CUDA kernels all introduce non-determinism).

-   Extraction pipelines (entity extraction, epistemic classification,
    intent extraction, emotional signal extraction) rely on NLP models
    that are not deterministic.

-   The RFCs acknowledge this by saying "extraction logic versions
    SHOULD be recorded" but this only defers the problem: two runs of
    the same extraction logic version on the same input may still
    diverge due to hardware non-determinism.

Recommendation: The determinism requirement should be scoped to the
storage layer (event log ordering, derived state computation from stored
events) rather than extended to extraction pipelines. The latter should
be treated as "best-effort deterministic" with reconciliation semantics,
not strict determinism.

**5.2 Absence of Empirical Validation**

The RFC suite is entirely theoretical. There are no cited benchmarks, no
ablation studies, no comparison with existing approaches (MemGPT,
LangChain memory, Zep, Cognee, etc.), and no data supporting any of the
design choices.

This is the single most significant weakness of the suite as a whole.
The hypothesis cannot be validated, the re-ranking weights cannot be
tuned, the decay functions cannot be calibrated, and the STR formula
cannot be optimized without empirical data.

Before RFC-0001 exits Draft status, the following minimum experimental
evidence should be required:

-   Retrieval accuracy benchmarks on a long-horizon conversational
    dataset comparing PRME against a baseline vector store.

-   Context compression metrics: does the organizer actually reduce
    context size over time, and by how much?

-   Supersedence correctness: what fraction of superseded facts are
    correctly suppressed in DEFAULT retrieval mode?

-   Latency benchmarks: what is the p50/p95/p99 retrieval latency for
    memory bundles of various sizes?

**5.3 Scalability Analysis**

The RFC suite targets "local-first, embeddable" systems but several
design choices will not scale beyond moderate volumes:

-   The append-only event log grows monotonically. At high event rates
    (e.g., continuous monitoring applications), this will reach storage
    limits without aggressive compaction. The compaction semantics are
    underspecified (see RFC-0010 notes).

-   Graph traversal (1-3 hops in RFC-0001) for every retrieval query
    will become expensive as the entity graph grows. A 3-hop traversal
    over a graph with 100,000 nodes can return millions of candidates.
    Pruning strategies are not specified.

-   The scheduled organizer (salience recalculation, deduplication,
    summarization) must process the entire memory store periodically. At
    scale, this will require incremental rather than batch processing.
    The RFCs do not address this.

-   Multi-agent environments (RFC-0011) where many agents write
    simultaneously will experience high contention on the event log.
    Concurrent write semantics are not specified for the DuckDB/Kùzu
    layer.

**5.4 Security Threat Model**

The security considerations across the RFCs are present but
inconsistent:

-   Reinforcement flooding is mentioned in RFC-0006, 0008, and 0011 but
    the rate-limiting mechanism is never specified. "Implementations
    SHOULD rate-limit reinforcement signals" is not a security
    specification.

-   Side-channel attacks via namespace isolation are acknowledged but
    not addressed. The problem of inferring the existence of information
    from its absence in search results (the "knowledge of nothing"
    problem) is real in vector database systems and requires specific
    mitigation.

-   The trust model in RFC-0011 has no mechanism for bootstrapping trust
    in a new agent. How does an agent establish an initial trust_score?
    This creates an onboarding vulnerability.

-   There is no discussion of memory poisoning --- deliberately
    injecting false information at high confidence to corrupt the memory
    system. This is a significant threat in any system that accepts
    external inputs.

**5.5 The \"Standards Track\" Designation**

All 15 RFCs are designated "Standards Track" despite all being in Draft
status and none having a reference implementation, test suite, or
empirical validation. This designation is premature and potentially
misleading.

Recommendations for status:

-   RFC-0001, 0003, 0005, 0010: Promote to Experimental (with a
    reference implementation requirement).

-   RFC-0002, 0004, 0006, 0007, 0008: Remain Draft with specific
    feedback items addressed.

-   RFC-0009, 0011, 0012, 0014, 0015: Downgrade to Informational until
    key gaps are resolved.

-   RFC-0013: Move to Withdrawn or Exploratory with a fundamental
    redesign requirement.

**6. Genuine Contributions Worth Preserving**

Despite the criticisms above, the RFC suite contains several ideas that
are genuinely novel or underspecified in the current literature and
deserve continued development:

**6.1 Epistemic Typing in Memory (RFC-0005)**

No production LLM memory system currently distinguishes between
OBSERVED, ASSERTED, INFERRED, HYPOTHETICAL, and DEPRECATED beliefs as
first-class data types. This is a real gap and RFC-0005 provides a
rigorous starting point.

**6.2 Deterministic Decay with Explicit Forgetting Policy (RFC-0003)**

Most memory systems either never forget or forget naively (TTL-based
deletion). The combination of decay profiles, tier transitions, and
explicit tombstone-based deletion with policy versioning is more
sophisticated than anything in current production tools and maps well
onto known cognitive science findings.

**6.3 Event-Sourced Memory with Deterministic Rebuild (RFC-0010)**

Applying event sourcing to AI memory is the right architectural choice
and is not commonly done. The portability and auditability properties
that follow from this are genuinely valuable, particularly for
enterprise deployments with compliance requirements.

**6.4 Signal-to-Token Ratio as a Retrieval Metric (RFC-0007)**

The STR framing is a useful addition to retrieval evaluation. Most
retrieval research optimizes for relevance; STR explicitly trades
relevance against verbosity, which is directly applicable to
constrained-context LLM deployment.

**6.5 Memory Branching for Safe Hypothetical Reasoning (RFC-0015)**

The concept of branch-scoped memory for simulation and planning without
contaminating canonical state is architecturally sound and has clear use
cases in planning agents. The execution needs hardening but the concept
is worth pursuing.

**7. Recommendations**

**7.1 Immediate Priority Actions**

-   Commission or produce a reference implementation of RFC-0001 (PRME
    core) before expanding the RFC suite further. Without an
    implementation, the later RFCs are building on untested foundations.

-   Establish empirical benchmarks: at minimum, a retrieval accuracy
    benchmark and a context compression benchmark. Every design claim in
    the suite should be backed by a measurable improvement.

-   Revise or withdraw RFC-0013. As written, it presents risks that
    outweigh its benefits. Emotional signal tracking should require a
    complete rethinking with mandatory opt-in, strict proxy-only
    framing, and independent ethical review.

-   Define tombstone format for RFC-0003 and specify merge conflict
    resolution for RFC-0002 before any implementation begins. These are
    blockers for interoperability.

**7.2 Medium-Term Improvements**

-   Address the determinism scope problem: constrain determinism
    requirements to the event storage and derivation layer, and define
    "best-effort deterministic" semantics for extraction pipelines.

-   Add scalability analysis to RFC-0001: graph traversal depth limits,
    organizer incremental processing, and concurrent write semantics
    under high agent counts.

-   Develop the trust model in RFC-0011 to be context-dependent rather
    than scalar. A per-domain trust model is more realistic and more
    secure.

-   Create a formal threat model covering memory poisoning,
    reinforcement flooding, side-channel namespace inference, and agent
    identity spoofing.

**7.3 Long-Term Research Questions**

-   Can epistemic state classification be performed reliably enough
    (\>90% accuracy) to be trusted as a system-level property? This is
    the fundamental empirical question for RFC-0005.

-   What are the optimal decay function parameters for different memory
    types and use cases? This requires longitudinal studies across
    diverse user populations.

-   How does the system behave when the underlying LLM is replaced?
    Embeddings change, extraction logic changes, and the entire derived
    state may need to be rebuilt. Migration paths are not addressed.

-   Is the "Memory as intelligence core" hypothesis testable? Can a
    system with PRME-style memory be shown to outperform a system
    without it on long-horizon reasoning tasks, and does the improvement
    scale with memory quality? This is the scientific validation the
    hypothesis requires.

**8. Final Verdict**

  --------------------- -------------------------------------------------
  **Hypothesis          PARTIALLY VALID --- Strong engineering claim,
  Validity**            overclaims as general intelligence theory

  **RFC Suite           COHERENT --- The foundational choices (event
  Architecture**        sourcing, graph + vector + lexical hybrid,
                        decay + epistemic typing) are sound

  **RFC Suite           EARLY DRAFT --- No reference implementation, no
  Readiness**           empirical validation, several RFCs have critical
                        gaps

  **Most Critical       RFC-0013 (Emotional Signals) as written could
  Risk**                cause psychological and privacy harm if deployed

  **Most Valuable       RFC-0005 (Epistemic State Model) and RFC-0003
  Contribution**        (Forgetting & Decay) --- genuinely novel and
                        practically important

  **Scalability**       UNPROVEN --- Design works for moderate scale;
                        graph traversal, organizer batch jobs, and
                        concurrent writes are not analyzed at scale

  **Recommended Next    Build a reference implementation of RFC-0001 +
  Step**                RFC-0003 + RFC-0005 + RFC-0010 and produce
                        benchmark data before expanding the RFC suite
                        further
  --------------------- -------------------------------------------------

The scientist behind this work has identified the right problem space.
LLM memory is genuinely broken in production, and the architectural
ideas here --- event sourcing, epistemic typing, decay with
reinforcement, cost-aware retrieval --- address real failure modes.

But ambition must be tempered by validation. The RFC suite is currently
a sophisticated theory looking for experimental confirmation. Push the
foundational pieces to implementation. Measure them. Then build the more
speculative extensions on ground that has been tested rather than
assumed.

The hypothesis as stated goes further than the evidence warrants. The
engineering program it motivates is worth pursuing. Those are compatible
conclusions, and they are the honest ones.

**End of Audit Report**

Auditor: Independent Scientist, AI Memory Systems --- February 19, 2026
