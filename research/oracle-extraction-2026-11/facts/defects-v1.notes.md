# defects-v1: cases the form could not express

Written for what each turn states, not for any question. 31 assertions on 14 turns, 8 of them with facts.

1. **Comparison in `prefers`.** "I prefer trains to flights" names a liked thing and a less liked thing. The form
   has no comparator, so only `Alex Rivera prefers trains` is written; the ranking against flights is lost.
2. **Purpose or context on a `uses` or `prefers` fact.** "I write Go for services and TypeScript for tooling",
   "an allergy card for restaurants". `role` on `uses` is for a manner ("learning", "reading", "writing"), not
   for the purpose, so the purpose phrases are lost. The allergy card keeps its phrase inside the object text.
3. **"We" with no stated membership.** In session 2, "We chose Postgres partitioning" is written as the owner
   alone, because this session never says who "we" is. In session 1, "we" is Alex and Jordan from the first turn,
   so decisions are written for both.
4. **Dates and time.** "late March next year", "before Friday", "on weekends" have no field. A date that is part
   of a stated value is kept inside the value ("late March next year", "runbook reviewed by Sam before Friday");
   "on weekends" is lost, because `uses Rust` with `status current` cannot say it is a weekend activity.
5. **A city as the destination of one trip.** "We chose Tokyo, Kyoto, Kanazawa, and Osaka" is written as each
   person deciding each city. The form cannot say the choice is a destination of the Japan trip; the trip is only
   linked to Japan.
6. **One statement with two subjects and a deadline.** "My manager Dana wants the runbook reviewed by Sam before
   Friday" becomes three assertions: Dana manages Alex, Dana wants the runbook reviewed, Sam is involved as
   reviewer. The link from the want to Sam's review exists only through the shared object text.
7. **A rejected idea that was never stated as a plan.** "We dropped the idea of self-hosting" is written as
   `decided self-hosting, negative`. That is the only way the form can say an idea was dropped, and it reads as
   if the idea had been on the table.
8. **Book against language.** "Rust for Rustaceans" and "Rust" are different things; the form keeps them apart only
   as two `uses` facts with different roles.

## Checker result on this file

`check_facts.py` accepts 9 of 31 assertions today. All 22 refusals are the pending rule change: an owner named by
"I", "me", "my", "we", "us" or "our", or a name mentioned in an earlier turn. No refusal is any other kind, so the
file is complete for the new rule and needs no edit once the checker has it.
