# chat-v1: cases the form could not express

58 assertions on 54 turns, 25 of them with facts, including two household inferences in turn 1.

1. **"Our" dog and "I live with".** "our golden retriever, Biscuit" speaks for Dana and Sam, so ownership is
   written twice. "I live in Denver with my partner Sam" is read as Sam living there too, which is an inference
   from "with"; if the build wants only stated facts, drop the Sam `lives_in Denver` assertion.
2. **No time on an employment or a race.** "I start as analytics manager on May 4", "the Cherry Creek Half
   Marathon on May 17", "I finished the Cherry Creek Half on Sunday". The race date is kept as `race_date` on the
   event; the start date has nothing to attach to (`works_at` has no start field), so "May 4" is lost.
3. **Duration and history.** "vegetarian for about six years", "I quit coffee last month", "two weeks in at
   Brightpath". Only the state is written (`diet`, `prefers`, `status`), never how long it has held.
4. **Comparisons.** "I'd rather use Python" than Tableau, "I already like my team more than the one at Northwind".
   `prefers` names the liked thing and cannot name the comparison; the second sentence is not written at all.
5. **A plan made for someone.** "she's making me a training plan": written as Melanie `involved_in` a training plan
   as planner. The form cannot say the plan is for Dana.
6. **A reason on a denial.** "Sam can't come because of work" is `involved_in Portland trip, negative`. The reason
   is lost, and so is the difference between a refusal and an impossibility.
7. **An animal as an actor.** "Biscuit is being a menace", "my dog ate one of Sam's running shoes". `involved_in`
   is defined for a person; Biscuit is written with `subject_type` concept. The dog's actions are the one place the
   entity types do not fit.
8. **Repeated statements by the assistant.** The assistant restates most facts ("Noted: vegetarian...", "peanuts are
   still out"). Rule 9 does not ask for restatements, and writing them again would duplicate the same fact with a
   later date, so a turn is left empty when it adds nothing, and only added facts are written (for example
   "Aunt Dana" in `s3-family-and-jobs#1`).
9. **A promise to follow up.** "I'll leave peanuts out of suggestions" is a promise by the assistant, which rule 9
   would make `decided` with the assistant as subject; the turn does not name the assistant, so nothing is written.

## Checker result on this file

`check_facts.py` accepts 26 of 58 assertions today. Every refusal (32) is the pending rule change: the owner named
by "I", "me", "my", "we", "us" or "our", or a name mentioned in an earlier turn ("Melanie" for "Mel" and "she").
No other kind of refusal. The file is complete for the new rule.
