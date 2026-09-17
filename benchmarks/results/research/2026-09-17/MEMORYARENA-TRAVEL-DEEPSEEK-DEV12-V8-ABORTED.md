# MemoryArena travel development trial V8 — aborted

V8 was stopped without a score after 18 of 156 traveler executions: both arms
completed group 140 and the PRME arm completed two travelers in group 76. No
transport or agent failure caused the stop.

The first group exposed an incomplete dependency-routing rule. Michael's query
asked to “stay at the same place as Audrey.” The registered router recognized
possessives and the words `join` and `with`, but did not recognize a plain prior
traveler name after `as`. Its context therefore contained the fixed base plan
but omitted Audrey's plan, and the model explicitly reported that Audrey's plan
was unavailable. Continuing would have measured a known adapter defect.

The replacement policy matches exact occurrences of every previously stored
traveler name in the constraint body while continuing to exclude the roster
preamble. The reproduced Michael case is covered by an adapter test. A new
registration is required because the adapter source and query policy changed.

- Registration SHA-256:
  `b75db22605992b5e2a29104e69af8e829d32295c0e7de5da5a782333e5abed76`
- Registered PRME source revision:
  `61374649d5457bcce2dab4afa3406b966434b17d`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`

This aborted development run carries no quality score and is not evidence for
or against the registered non-inferiority gate.
