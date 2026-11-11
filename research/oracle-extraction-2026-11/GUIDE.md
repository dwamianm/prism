# Perfect extraction test: guide

**Question.** If extraction wrote correct facts, would PRME answer the probe questions? If yes, extraction is the
limit and a specialized extraction model is worth building. If no, the read side is the limit and a better
extractor would not help.

**Method.** A person (here, an agent that has not seen the probes' questions or answer keys) writes the facts of
each turn by hand, following this guide. The facts are loaded with `ingest(assertions=...)`, which calls no
model. The repository's three chat probes are then run on those packs.

This guide is also the first draft of the target format a specialized extraction model would be trained to
produce. Where a rule below cannot be followed with the current assertion form, write the case in
`facts/<name>.notes.md` instead of bending the rule; those notes are a result.

## Pass bars, fixed before any fact is written

"Extraction is the limit" is supported when, with the facts of level O2 and `memory_mode="address_first"`:

- defect probes: the answer is in the top 3 for 7 of 7;
- chat probe: top 3 for at least 17 of 18, and at least 2 more than model extraction with the mode on (15);
- member probe: top 3 for at least 11 of 12.

Anything less is reported as "not supported", with the questions that failed and what ranked above them.

## Who sees what

- The annotator reads `turns/<name>.json` (turns only) and this guide. The annotator does not open
  `benchmarks/conversations/`, `BENCHMARKS.md`, `benchmarks/results/`, any probe output, or `tests/contract*`.
- The annotator writes facts for what each turn says, not for what a question might ask. Every statement of a
  turn gets its facts, including the ones that look unimportant.

## The form

`facts/<name>.json`: `{"turns": [{"session": id, "index": n, "assertions": [...]}, ...]}`, one entry per user or
assistant turn, in order. An assertion:

| Field | Meaning |
|---|---|
| `subject`, `subject_type` | The entity the fact is about. Types: person, organization, location, product, concept, event |
| `relation` | One of the vocabulary in `src/prme/retrieval/relations.py` (`RELATIONS`), or `has` for a literal attribute |
| `predicate` | With `has` only: the attribute's name (rule 3) |
| `object`, `object_type` | The other entity, or the literal value (no type for a literal) |
| `role` | The family word for `kin_of` (sister), the title for `works_at` (nurse) |
| `status` | `current`, `planned`, `done` (a completed event), `past` (it was true and no longer is) |
| `polarity` | `positive` unless the turn denies the fact |
| `level` | `O1` or `O2` (rule 8). Not part of the engine's form; the loader strips it |

## Rules

1. **Name the subject.** Never "I", "we", "my" or a pronoun. The owner's name is in the turns file
   (`user_name`). "We adopted a dog" is one assertion per person it speaks for, when the turns say who "we" is;
   otherwise the owner alone.
2. **One fact per assertion.** "I live in Denver with my partner Sam and our dog Biscuit" is four or more
   assertions: where the owner lives, the partner, the dog, and what kind of dog.
3. **One name per attribute, in the whole conversation.** Keep `facts/<name>.attributes.md`: each `has`
   predicate you use, with one line saying what it means. Before inventing a predicate, look for one in that
   file that means the same. `diet`, not `diet` in one turn and `eating_habits` in another.
4. **Prefer a relation of the vocabulary to `has`.** A job is `works_at` with the title as `role`, not
   `has occupation`. A home is `lives_in`. A pet is `owns`. An allergy or an intolerance is `allergic_to`.
5. **A state is not an event.** What holds over time is `current` ("works at the clinic"). What happened once is
   `done` ("bought shoes"). A plan or an intention is `planned`. Something only considered ("thinking about
   getting a dog") is never written with the relation it would have if it were true (`owns`); write it as
   `has considering` with the thing as the value, status `current`.
6. **Polarity is for denial only.** "She can't eat gluten" is `allergic_to gluten`, positive. "I'm not
   vegetarian anymore" is the diet fact with status `past` (rule 8), not a negative fact. Use `negative` only
   when the turn denies something that was never stated as true ("I don't own a car").
7. **Use the turn's own words for names and values**, without a leading article. The loader checks that the
   content mentions each subject and object; a speaker's own "I" counts as a mention of the speaker. When a
   value is implied and not written, put the case in the notes file and leave it out.
8. **Two levels.**
   - `O1`: what this turn states, read alone. A perfect reader of one turn.
   - `O2`: what this turn ends. When a turn makes an earlier fact untrue, add the earlier fact again with status
     `past` (same subject, relation, role and object as it was stated), marked `O2`. "I quit the hospital" after
     "I work as a nurse at St. Mary's hospital" adds `works_at St. Mary's hospital, role nurse, past`. A fact
     that depended on it goes too, when the turn or common sense makes that plain (a night-shift dinner time
     after a move to day shifts); put doubtful cases in the notes file and do not close them.
   The O2 object must still be mentioned in the turn that closes it (rule 7). When it is not ("I quit" with no
   place named), write the closure anyway, mark it `O2`, and list it in the notes: the loader will report how
   many closures the mention check refuses, and that count is a finding about the form.
9. **Assistant turns.** Facts the assistant states about the world or the user are written as for any turn. What
   the assistant recommends or promises is `decided` with the assistant as subject when the turn names the
   assistant; otherwise leave the turn without assertions (it is still stored as text).
10. **Dates.** A fact about a named date keeps the date in its value ("starts on November 2"). The form has no
    time field of its own; note each case where that loses something.

## What is reported

For each probe: top 3 and top 5, for these arms.

| Arm | Facts | Mode |
|---|---|---|
| M0 | model extraction (the saved off build) | off |
| M1 | model extraction | address_first |
| A0 | O1 | off |
| A1 | O1 | address_first |
| B0 | O1 and O2 | off |
| B1 | O1 and O2 | address_first |

Plus: how many assertions per turn, how many the loader refused and why, the attribute lists, the notes files,
and for every probe question that fails under B1, what ranked above the answer.
