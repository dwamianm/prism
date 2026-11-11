# member-v1: cases the form could not express

Round 2 (2026-11-11): the assertion form now has an optional `time` field, so a date certain to the day is no
longer in a value and a fact may be about someone the turn does not name in certain cases. See
CHANGES-round2.md for what changed in this file; the notes below are round 1's, kept as written.

24 assertions on 30 turns, 12 of them with facts. No closures (O2): nothing in this conversation makes an earlier
fact untrue.

1. **A plan has no entity.** The plan itself (its name, its network) is never named, so every plan fact is written
   as an attribute of the member: deductible, family deductible, out-of-pocket maximum, coverage, prior
   authorization. "In network" and "out of network" are conditions on those facts and have no field; they are lost.
2. **An unborn child.** "How do I enroll my newborn" names a child who is not yet an entity, and the 30-day
   enrollment window that follows is written on Maya. Any dependent count taken from the facts will say two.
3. **A plan against a stated partner.** "I am getting married" arrives when a husband, Theo, is already stated.
   The form has no way to say this is a ceremony or a change of status, so the marriage event is written as planned
   and nothing is closed. The two facts read as a contradiction and are not one.
4. **A coworker.** "My coworker Dana" is a work relationship the vocabulary has no relation for (`friend_of`,
   `member_of`, `manager_of` all say something different). Nothing is written for it, so Dana reaches the store
   only as a name in the text of the turn.
5. **Two kinds of statement in one fact.** The assistant's "Your individual deductible is $1,500 and the family
   deductible is $3,000 for in-network care" is a rule of the plan, not something the member said; it is written as
   a fact about her because the form has no plan to attach it to. A reader cannot tell a member's statement from a
   plan rule that the assistant supplied.
6. **A separate plan with its own premium.** "Dental is a separate plan with its own premium" becomes
   `coverage dental, negative`. That says the plan does not include dental; it cannot say dental exists as another
   plan, nor carry the premium.
7. **Questions and restatements.** Turns that ask something, or restate what was already written ("Your dependents
   are Theo, your husband, and Nico, your child"), are left empty rather than duplicated, as in the other two
   conversations.

## Checker result on this file

`check_facts.py` accepts 0 of 24 assertions today, and every refusal is the pending rule change: the owner named by
"I" and "my" in a user turn, and by "your" in an assistant turn. No other kind of refusal.
