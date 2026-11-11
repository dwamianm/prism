## Top 3 and top 5 per arm

| Probe | M0 | M1 | A0 | A1 | B0 | B1 | Bar (B1 top 3) | Result |
|---|---|---|---|---|---|---|---|---|
| defects-v1 | 7/7 / 7/7 | 7/7 / 7/7 | 6/7 / 7/7 (= B) | 6/7 / 7/7 (= B) | 6/7 / 7/7 | 6/7 / 7/7 | 7 | fail |
| chat-v1 | 14/18 / 15/18 | 15/18 / 16/18 | 10/18 / 15/18 | 13/18 / 13/18 | 10/18 / 15/18 | 13/18 / 13/18 | 17 | fail |
| member-v1 | 10/12 / 11/12 | 11/12 / 12/12 | 9/12 / 11/12 (= B) | 10/12 / 11/12 (= B) | 9/12 / 11/12 | 10/12 / 11/12 | 11 | fail |

## What each build stored

| Probe | Arm | Active claims | User claims on the named owner | Entities | Facts loaded / written | Model calls | Empty stub extractions |
|---|---|---:|---:|---:|---:|---:|---:|
| defects-v1 | M0 | 24 | 12 | 25 | model | 0 | - |
| defects-v1 | M1 | 24 | 12 | 25 | model | 0 | - |
| defects-v1 | B0 | 31 | 17 | 25 | 31 / 31 | 0 | 6 |
| defects-v1 | B1 | 31 | 17 | 25 | 31 / 31 | 0 | 6 |
| chat-v1 | M0 | 101 | 35 | 58 | model | 0 | - |
| chat-v1 | M1 | 101 | 35 | 58 | model | 0 | - |
| chat-v1 | A0 | 51 | 23 | 27 | 53 / 54 | 0 | 30 |
| chat-v1 | A1 | 51 | 23 | 27 | 53 / 54 | 0 | 30 |
| chat-v1 | B0 | 52 | 24 | 28 | 57 / 58 | 0 | 29 |
| chat-v1 | B1 | 52 | 24 | 28 | 57 / 58 | 0 | 29 |
| member-v1 | M0 | 32 | 7 | 36 | model | 0 | - |
| member-v1 | M1 | 32 | 7 | 36 | model | 0 | - |
| member-v1 | B0 | 9 | 5 | 4 | 9 / 24 | 0 | 24 |
| member-v1 | B1 | 9 | 5 | 4 | 9 / 24 | 0 | 24 |

## Loader: accepted and refused, by the part of the mention rule each needed

| Probe | Levels | Written | Accepted | By the turn's text alone | Needing owner | Carried | Closure | Refused (by reason) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| defects-v1 | O1+O2 | 31 | 31 | 9 | 16 | 6 | 0 | none |
| chat-v1 | O1 | 54 | 53 | 26 | 23 | 5 | 0 | literal value not in the turn: 1 |
| chat-v1 | O1+O2 | 58 | 57 | 26 | 27 | 5 | 1 | literal value not in the turn: 1 |
| member-v1 | O1+O2 | 24 | 9 | 0 | 9 | 0 | 0 | subject not in the turn, not the owner, not named earlier: 11; object not in the turn, not the owner, not named earlier: 4 |

## B1 rebuilt from scratch

- defects-v1: identical (probe rows, graph measures, every packed context)
- chat-v1: context allergy.txt differs; context coffee.txt differs; context diet.txt differs; context job-now.txt differs; context language.txt differs; context niece-born.txt differs; context niece-name.txt differs; context race-time.txt differs; context sister-city.txt differs; context start-date.txt differs; context title-now.txt differs
- member-v1: context coworker.txt differs; context deductible.txt differs; context dependents-first-person.txt differs; context dependents.txt differs; context diagnosis-first-person.txt differs; context diagnosis.txt differs; context husband.txt differs; context marriage.txt differs; context pregnant.txt differs; context trip-when.txt differs; context trip.txt differs

## How B1 serves each question (receipts)

| Probe | Question | Answer rank | Path | Relations | Ledger |
|---|---|---:|---|---|---|
| defects-v1 | Which cities will Alex and Jordan visit in Japan? | 5 | routed | visits | route: current, current |
| defects-v1 | What coding language is Alex learning? | 3 | routed | uses | route: current, current, current, current |
| defects-v1 | Which languages does Alex write at work? | 1 | catalog |  | catalog: current |
| defects-v1 | How does Alex prefer to travel in Japan? | 2 | routed | prefers, visits | route: current, current, current, current, current |
| defects-v1 | Who is allergic to shellfish? | 1 | routed | allergic_to | route: current |
| defects-v1 | What did the team choose for the shipment table? | 1 | routed | member_of, part_of |  |
| defects-v1 | Who should review the runbook? | 1 | text ranking only |  |  |
| chat-v1 | Where does Dana work now? | 1 | routed | works_at | route: current, ended |
| chat-v1 | What is Dana's job title now? | 7 | routed | works_at | route: current, ended |
| chat-v1 | When did Dana start the new job? | 10 | catalog |  | catalog: current |
| chat-v1 | Who was Dana's manager at Northwind? | 1 | routed | manager_of (manager) | route: current |
| chat-v1 | What does Dana's partner do for work? | 2 | routed | partner_of, works_at | route: current |
| chat-v1 | What is Sam allergic to? | 1 | routed | allergic_to | route: current |
| chat-v1 | What kind of dog does Dana have, and what is it called? | 9 | routed | involved_in | route: current, current, current, current, current |
| chat-v1 | What is the name of Dana's niece? | 2 | catalog |  | catalog: current |
| chat-v1 | When was Ivy born? | none | catalog |  | catalog: current, current |
| chat-v1 | Where does Dana's sister live? | 2 | routed | kin_of (sister), lives_in | route: current |
| chat-v1 | Who is Dana's running partner? | 1 | routed | friend_of, partner_of | route: current, current |
| chat-v1 | What was Dana's half marathon finishing time? | 3 | catalog |  | catalog: current |
| chat-v1 | Does Dana drink coffee? | 9 | catalog |  | catalog: current, ended |
| chat-v1 | What is Dana's diet now? | 1 | catalog |  | catalog: current, ended |
| chat-v1 | What news did Melanie share after the race? | 1 | catalog |  | catalog: current |
| chat-v1 | What is Dana planning for July? | 1 | catalog |  | catalog: current |
| chat-v1 | Where will Dana stop overnight on the drive? | 1 | catalog |  | catalog: current, current |
| chat-v1 | Which programming language does Dana prefer? | 2 | routed | prefers | route: current, current, ended |
| member-v1 | Who is Maya's husband? | 1 | routed | partner_of | route: current |
| member-v1 | What is the name of Maya's child? | 1 | routed | kin_of (child/daughter/kid/son/stepchild/stepdaughter/stepson) |  |
| member-v1 | What are the names of Maya's dependents? | 2 | routed | kin_of (child/daughter/kid/son/stepchild/stepdaughter/stepson), partner_of | route: current, current |
| member-v1 | Do you remember the names of my dependents? | 2 | routed | kin_of (child/daughter/kid/son/stepchild/stepdaughter/stepson), partner_of | route: current, current |
| member-v1 | What was Maya diagnosed with? | 1 | catalog |  | catalog: current |
| member-v1 | What did I tell you about my health? | 1 | catalog |  | catalog: current |
| member-v1 | Where is Maya planning a trip? | 6 | catalog |  | catalog: current |
| member-v1 | When is Maya's trip to Jamaica? | 2 | catalog |  | catalog: current |
| member-v1 | Is Maya expecting a baby? | 3 | catalog |  | catalog: current |
| member-v1 | What life event did Maya mention? | 5 | catalog |  | catalog: current |
| member-v1 | What is my deductible? | 1 | catalog |  | catalog: current |
| member-v1 | What did Maya's coworker Dana ask about? | 1 | catalog |  | catalog: current |

## Questions B1 misses in the top 3: its top 5

**defects-v1: Which cities will Alex and Jordan visit in Japan?** answer rank 5, routed

1. (fact) Hi, I am Alex Rivera. My partner Jordan and I are planning a trip to Japan in late March next year.
2. (fact) That is cherry blossom season.
3. (fact) Trip to Japan trip timing late March next year.
4. (fact) My partner Jordan and I are planning a trip to Japan in late March next year.
5. (fact) We chose Tokyo, Kyoto, Kanazawa, and Osaka.

**chat-v1: What is Dana's job title now?** answer rank 7, routed

1. (fact) Two weeks in at Brightpath and I already like my team more than the one at Northwind.
2. (fact) I gave notice at Northwind yesterday.
3. (fact) Green tea now.
4. (note) Will do. The new job is going well so far. Two weeks in at Brightpath and I already like my team more than the one at Northwind.
5. (note) Thanks! Rachel will love that. She keeps asking what Ivy's aunt has been up to.

**chat-v1: When did Dana start the new job?** answer rank 10, catalog

1. (fact) Priya manager of Dana.
2. (fact) I'm Dana. I just started using this assistant, so here's a bit about me: I live in Denver with my partner Sam and our golden retriever, Biscuit.
3. (fact) I had a first interview with Brightpath on Monday for an analytics manager role.
4. (fact) Rachel lives in Portland, so I haven't met Ivy yet.
5. (fact) Congratulations, Aunt Dana! Welcome to the world, Ivy.

**chat-v1: What kind of dog does Dana have, and what is it called?** answer rank 9, routed

1. (fact) I'm planning the Portland trip for July to finally meet Ivy.
2. (fact) I'm so happy I got under two hours.
3. (fact) Training update: Mel and I did a 10 mile long run on Saturday.
4. (fact) Dana involved in first interview.
5. (fact) I had a first interview with Brightpath on Monday for an analytics manager role.

**chat-v1: When was Ivy born?** answer rank none, catalog

1. (fact) Rachel lives in Portland, so I haven't met Ivy yet.
2. (fact) Big news: my sister Rachel had her baby last Thursday! A girl named Ivy.
3. (fact) Congratulations, Aunt Dana! Welcome to the world, Ivy.
4. (fact) I'm planning the Portland trip for July to finally meet Ivy.
5. (fact) I'm planning the Portland trip for July to finally meet Ivy. Sam can't come because of work, so it'll be just me and Biscuit.

**chat-v1: Does Dana drink coffee?** answer rank 9, catalog

1. (fact) Rachel lives in Portland, so I haven't met Ivy yet.
2. (fact) Oh, and a food update: I've started eating fish again, so I guess I'm pescatarian now, not vegetarian.
3. (fact) Dana diet vegetarian.
4. (fact) I'm Dana. I just started using this assistant, so here's a bit about me: I live in Denver with my partner Sam and our golden retriever, Biscuit.
5. (fact) Priya manager of Dana.

**member-v1: Where is Maya planning a trip?** answer rank 6, catalog

1. (fact) My husband is Theo and my child is Nico.
2. (fact) Your individual deductible is $1,500 and the family deductible is $3,000 for in-network care.
3. (fact) I am pregnant, how does my plan cover this?
4. (fact) You can add a newborn within 30 days of the birth as a qualifying life event.
5. (fact) Open enrollment is coming up.

**member-v1: What life event did Maya mention?** answer rank 5, catalog

1. (fact) My husband is Theo and my child is Nico.
2. (fact) You can add a newborn within 30 days of the birth as a qualifying life event.
3. (fact) Your individual deductible is $1,500 and the family deductible is $3,000 for in-network care.
4. (note) What is the first question I asked you today?
5. (note) I am getting married. What plan will be best for our new family?

