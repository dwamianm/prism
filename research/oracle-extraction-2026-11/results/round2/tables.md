## Top 3 and top 5 per arm

| Probe | M0 | M1 | A0 | A1 | B0 | B1 | Bar (B1 top 3) | Result |
|---|---|---|---|---|---|---|---|---|
| defects-v1 | 7/7 / 7/7 | 7/7 / 7/7 | 4/7 / 5/7 (= B) | 5/7 / 5/7 (= B) | 4/7 / 5/7 | 5/7 / 5/7 | 7 | fail |
| chat-v1 | 14/18 / 15/18 | 15/18 / 16/18 | 8/18 / 11/18 | 12/18 / 13/18 | 7/18 / 11/18 | 12/18 / 13/18 | 17 | fail |
| member-v1 | 10/12 / 11/12 | 11/12 / 12/12 | 8/12 / 9/12 (= B) | 9/12 / 9/12 (= B) | 8/12 / 9/12 | 9/12 / 9/12 | 11 | fail |

## What each build stored

| Probe | Arm | Active claims | User claims on the named owner | Entities | Facts loaded / written | Model calls | Empty stub extractions |
|---|---|---:|---:|---:|---:|---:|---:|
| defects-v1 | M0 | 24 | 12 | 25 | model | 0 | - |
| defects-v1 | M1 | 24 | 12 | 25 | model | 0 | - |
| defects-v1 | B0 | 31 | 17 | 25 | 31 / 31 | 0 | 6 |
| defects-v1 | B1 | 31 | 17 | 25 | 31 / 31 | 0 | 6 |
| chat-v1 | M0 | 101 | 35 | 58 | model | 0 | - |
| chat-v1 | M1 | 101 | 35 | 58 | model | 0 | - |
| chat-v1 | A0 | 52 | 25 | 27 | 53 / 53 | 0 | 30 |
| chat-v1 | A1 | 52 | 25 | 27 | 53 / 53 | 0 | 30 |
| chat-v1 | B0 | 55 | 28 | 28 | 57 / 57 | 0 | 29 |
| chat-v1 | B1 | 55 | 28 | 28 | 57 / 57 | 0 | 29 |
| member-v1 | M0 | 32 | 7 | 36 | model | 0 | - |
| member-v1 | M1 | 32 | 7 | 36 | model | 0 | - |
| member-v1 | B0 | 23 | 8 | 6 | 23 / 23 | 0 | 18 |
| member-v1 | B1 | 23 | 8 | 6 | 23 / 23 | 0 | 18 |

## Loader: accepted and refused, by the part of the mention rule each needed

| Probe | Levels | Written | Accepted | text only | speaker | memory | closure | Refused (by reason) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| defects-v1 | O1+O2 | 31 | 31 | 9 | 0 | 22 | 0 | none |
| chat-v1 | O1 | 53 | 53 | 25 | 0 | 28 | 0 | none |
| chat-v1 | O1+O2 | 57 | 57 | 25 | 0 | 32 | 1 | none |
| member-v1 | O1+O2 | 23 | 23 | 0 | 8 | 15 | 0 | none |

## B1 rebuilt from scratch

- defects-v1: context reviewer.txt differs; context shipments.txt differs; context work-languages.txt differs
- chat-v1: probe rows differ; context allergy.txt differs; context coffee.txt differs; context diet.txt differs; context dog.txt differs; context job-now.txt differs; context language.txt differs; context mel-news.txt differs; context niece-born.txt differs; context niece-name.txt differs; context old-manager.txt differs; context race-time.txt differs; context running-partner.txt differs; context sister-city.txt differs; context start-date.txt differs; context title-now.txt differs; context trip.txt differs
- member-v1: context coworker.txt differs; context dependents-first-person.txt differs; context dependents.txt differs; context diagnosis-first-person.txt differs; context diagnosis.txt differs; context marriage.txt differs

## How B1 serves each question (receipts)

| Probe | Question | Answer rank | Path | Relations | Ledger |
|---|---|---:|---|---|---|
| defects-v1 | Which cities will Alex and Jordan visit in Japan? | 25 | routed | visits | route: current, current |
| defects-v1 | What coding language is Alex learning? | 3 | routed | uses | route: current, current, current, current |
| defects-v1 | Which languages does Alex write at work? | 18 | catalog |  | catalog: current, current, current, current |
| defects-v1 | How does Alex prefer to travel in Japan? | 2 | routed | prefers, visits | route: current, current, current, current, current |
| defects-v1 | Who is allergic to shellfish? | 1 | routed | allergic_to | route: current |
| defects-v1 | What did the team choose for the shipment table? | 1 | routed | member_of, part_of | route: current |
| defects-v1 | Who should review the runbook? | 1 | catalog |  | catalog: current |
| chat-v1 | Where does Dana work now? | 1 | routed | works_at | route: current, earlier, ended, ended |
| chat-v1 | What is Dana's job title now? | 1 | routed | works_at | route: current, earlier, ended, ended |
| chat-v1 | When did Dana start the new job? | 22 | catalog |  | catalog: current, current, current, current, current |
| chat-v1 | Who was Dana's manager at Northwind? | 1 | routed | manager_of (manager) | route: current |
| chat-v1 | What does Dana's partner do for work? | 2 | routed | partner_of, works_at | route: current |
| chat-v1 | What is Sam allergic to? | 1 | routed | allergic_to | route: current |
| chat-v1 | What kind of dog does Dana have, and what is it called? | 21 | routed | involved_in | route: current, current, current, current, current |
| chat-v1 | What is the name of Dana's niece? | 7 | catalog |  | catalog: current, current |
| chat-v1 | When was Ivy born? | 1 | catalog |  | catalog: current, current |
| chat-v1 | Where does Dana's sister live? | 2 | routed | kin_of (sister), lives_in | route: current |
| chat-v1 | Who is Dana's running partner? | 1 | routed | friend_of, partner_of | route: current, current |
| chat-v1 | What was Dana's half marathon finishing time? | 10 | catalog |  | catalog: current, current, current, current, current |
| chat-v1 | Does Dana drink coffee? | 1 | catalog |  | catalog: current, current, ended |
| chat-v1 | What is Dana's diet now? | 1 | catalog |  | catalog: current, ended |
| chat-v1 | What news did Melanie share after the race? | 4 | catalog |  | catalog: current |
| chat-v1 | What is Dana planning for July? | 22 | catalog |  | catalog: current, current, current, current, current |
| chat-v1 | Where will Dana stop overnight on the drive? | 3 | catalog |  | catalog: current |
| chat-v1 | Which programming language does Dana prefer? | 2 | routed | prefers | route: current, current, ended |
| member-v1 | Who is Maya's husband? | 1 | routed | partner_of | route: current |
| member-v1 | What is the name of Maya's child? | 1 | routed | kin_of (child/daughter/kid/son/stepchild/stepdaughter/stepson) | route: current |
| member-v1 | What are the names of Maya's dependents? | 7 | routed | kin_of (child/daughter/kid/son/stepchild/stepdaughter/stepson), partner_of | route: current, current |
| member-v1 | Do you remember the names of my dependents? | 8 | routed | kin_of (child/daughter/kid/son/stepchild/stepdaughter/stepson), partner_of | route: current, current |
| member-v1 | What was Maya diagnosed with? | 1 | catalog |  | catalog: current |
| member-v1 | What did I tell you about my health? | 3 | text ranking only |  |  |
| member-v1 | Where is Maya planning a trip? | 1 | catalog |  | catalog: current, current |
| member-v1 | When is Maya's trip to Jamaica? | 3 | catalog |  | catalog: current, current |
| member-v1 | Is Maya expecting a baby? | 1 | catalog |  | catalog: current |
| member-v1 | What life event did Maya mention? | 8 | catalog |  | catalog: current, current |
| member-v1 | What is my deductible? | 1 | catalog |  | catalog: current |
| member-v1 | What did Maya's coworker Dana ask about? | 1 | catalog |  | catalog: current, current |

## Questions B1 misses in the top 3: its top 5

**defects-v1: Which cities will Alex and Jordan visit in Japan?** answer rank 25, routed

1. (fact) Trip to Japan's trip season is cherry blossom season.
2. (fact) Trip to Japan's trip timing is late March next year.
3. (fact) Jordan is going to be involved in trip to Japan (planner).
4. (fact) Trip to Japan is going to be located in Japan.
5. (fact) Alex Rivera is the partner of Jordan.

**defects-v1: Which languages does Alex write at work?** answer rank 18, catalog

1. (fact) Alex Rivera uses Go (writing).
2. (fact) Alex Rivera uses TypeScript (writing).
3. (fact) Alex Rivera uses Rust (learning).
4. (fact) Alex Rivera uses Rust for Rustaceans (reading).
5. (fact) Alex Rivera is the partner of Jordan.

**chat-v1: When did Dana start the new job?** answer rank 22, catalog

1. (fact) Priya is the manager of Dana (manager).
2. (fact) Dana is the partner of Sam.
3. (fact) Dana uses Tableau.
4. (fact) Dana lives in Denver.
5. (fact) Dana used to prefer coffee.

**chat-v1: What kind of dog does Dana have, and what is it called?** answer rank 21, routed

1. (fact) Dana is going to be involved in Portland trip (planner).
2. (fact) Dana was involved in Cherry Creek Half Marathon (runner) (on 2026-05-17).
3. (fact) Dana was involved in 10 mile long run (runner) (on 2026-04-11).
4. (fact) Dana was involved in analytics manager role (candidate) (on 2026-03-16).
5. (fact) Dana was involved in first interview (candidate) (on 2026-03-16).

**chat-v1: What is the name of Dana's niece?** answer rank 7, catalog

1. (fact) Dana is the partner of Sam.
2. (fact) Dana is a friend of Melanie.
3. (fact) Priya is the manager of Dana (manager).
4. (fact) Dana's diet is pescatarian.
5. (fact) Melanie is the partner of Tom.

**chat-v1: What was Dana's half marathon finishing time?** answer rank 10, catalog

1. (fact) Dana is going to be involved in Portland trip (planner).
2. (fact) Dana was involved in Cherry Creek Half Marathon (runner) (on 2026-05-17).
3. (fact) Dana was involved in 10 mile long run (runner) (on 2026-04-11).
4. (fact) Dana was involved in analytics manager role (candidate) (on 2026-03-16).
5. (fact) Dana was involved in first interview (candidate) (on 2026-03-16).

**chat-v1: What news did Melanie share after the race?** answer rank 4, catalog

1. (fact) Melanie's race result is 1:41.
2. (fact) Melanie's race count is ten half marathons.
3. (fact) Melanie is the partner of Tom.
4. (note) Melanie finished in 1:41, she's so fast. And she told me after the race that she got engaged to her boyfriend Tom last weekend!
5. (fact) Melanie was involved in 10 mile long run (running partner) (on 2026-04-11).

**chat-v1: What is Dana planning for July?** answer rank 22, catalog

1. (fact) Portland trip's trip month is going to be July.
2. (fact) Dana is going to visit Portland.
3. (fact) Dana is going to visit Boise.
4. (fact) Dana is going to be involved in Portland trip (planner).
5. (fact) Dana was involved in Cherry Creek Half Marathon (runner) (on 2026-05-17).

**member-v1: What are the names of Maya's dependents?** answer rank 7, routed

1. (fact) Maya Okafor is related to Nico (child).
2. (fact) Maya Okafor is the partner of Theo.
3. (note) Do you remember the names of my dependents?
4. (note) Do you have information on my dependents?
5. (fact) Maya Okafor's coverage is Prenatal visits are covered in full in network.

**member-v1: Do you remember the names of my dependents?** answer rank 8, routed

1. (fact) Maya Okafor is related to Nico (child).
2. (fact) Maya Okafor is the partner of Theo.
3. (note) Do you remember the names of my dependents?
4. (note) Do you have information on my dependents?
5. (note) Hi Kai. Do you remember me?

**member-v1: What life event did Maya mention?** answer rank 8, catalog

1. (fact) Maya Okafor's health state is pregnant.
2. (fact) Maya Okafor's coverage is radiation.
3. (fact) Maya Okafor is related to Nico (child).
4. (fact) Maya Okafor is the partner of Theo.
5. (fact) Maya Okafor is going to visit Jamaica.


## B1 against B1s per question (rank, then path; union rank from union_ranks.py)

**defects-v1**

| Question | M1 | Round 1 B1 | B1 | B1 union | B1s | B1s union |
|---|---:|---:|---|---:|---|---:|
| Which cities will Alex and Jordan visit in Japan? | 2 | 5 | 25 (routed: visits) | 12 | 5 (routed: visits) | 5 |
| What coding language is Alex learning? | 3 | 3 | 3 (routed: uses) | 3 | 3 (routed: uses) | 3 |
| Which languages does Alex write at work? | 2 | 1 | 18 (catalog) | 2 | 1 (catalog) | 1 |
| How does Alex prefer to travel in Japan? | 3 | 2 | 2 (routed: prefers, visits) | 2 | 1 (routed: prefers, visits) | 1 |
| Who is allergic to shellfish? | 1 | 1 | 1 (routed: allergic_to) | 1 | 1 (routed: allergic_to) | 1 |
| What did the team choose for the shipment table? | 1 | 1 | 1 (routed: member_of, part_of) | 1 | 1 (routed: member_of, part_of) | 1 |
| Who should review the runbook? | 1 | 1 | 1 (catalog) | 1 | 1 (catalog) | 1 |

**chat-v1**

| Question | M1 | Round 1 B1 | B1 | B1 union | B1s | B1s union |
|---|---:|---:|---|---:|---|---:|
| Where does Dana work now? | 2 | 1 | 1 (routed: works_at) | 1 | 1 (routed: works_at) | 1 |
| What is Dana's job title now? | 12 | 7 | 1 (routed: works_at) | 1 | 9 (routed: works_at) | 9 |
| When did Dana start the new job? | 2 | 10 | 22 (catalog) | 22 | 4 (catalog) | 4 |
| Who was Dana's manager at Northwind? | 3 | 1 | 1 (routed: manager_of (manager)) | 1 | 1 (routed: manager_of (manager)) | 1 |
| What does Dana's partner do for work? | 2 | 2 | 2 (routed: partner_of, works_at) | 2 | 1 (routed: partner_of, works_at) | 1 |
| What is Sam allergic to? | 1 | 1 | 1 (routed: allergic_to) | 1 | 1 (routed: allergic_to) | 1 |
| What kind of dog does Dana have, and what is it called? | 3 | 9 | 21 (routed: involved_in) | 21 | 6 (routed: involved_in) | 6 |
| What is the name of Dana's niece? | 1 | 2 | 7 (catalog) | 7 | 1 (catalog) | 1 |
| When was Ivy born? | none | none | 1 (catalog) | 1 | none (catalog) | none |
| Where does Dana's sister live? | 2 | 2 | 2 (routed: kin_of (sister), lives_in) | 2 | 2 (routed: kin_of (sister), lives_in) | 2 |
| Who is Dana's running partner? | 2 | 1 | 1 (routed: friend_of, partner_of) | 1 | 1 (routed: friend_of, partner_of) | 1 |
| What was Dana's half marathon finishing time? | 1 | 3 | 10 (catalog) | 10 | 2 (catalog) | 2 |
| Does Dana drink coffee? | 5 | 9 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| What is Dana's diet now? | 1 | 1 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| What news did Melanie share after the race? | 2 | 1 | 4 (catalog) | 4 | 1 (catalog) | 1 |
| What is Dana planning for July? | 1 | 1 | 22 (catalog) | 14 | 1 (catalog) | 1 |
| Where will Dana stop overnight on the drive? | 3 | 1 | 3 (catalog) | 3 | 1 (catalog) | 1 |
| Which programming language does Dana prefer? | 3 | 2 | 2 (routed: prefers) | 2 | 2 (routed: prefers) | 2 |

**member-v1**

| Question | M1 | Round 1 B1 | B1 | B1 union | B1s | B1s union |
|---|---:|---:|---|---:|---|---:|
| Who is Maya's husband? | 1 | 1 | 1 (routed: partner_of) | 1 | 1 (routed: partner_of) | 1 |
| What is the name of Maya's child? | 1 | 1 | 1 (routed: kin_of (child)) | 1 | 1 (routed: kin_of (child)) | 1 |
| What are the names of Maya's dependents? | 1 | 2 | 7 (routed: kin_of (child), partner_of) | 2 | 1 (routed: kin_of (child), partner_of) | 1 |
| Do you remember the names of my dependents? | 1 | 2 | 8 (routed: kin_of (child), partner_of) | 2 | 1 (routed: kin_of (child), partner_of) | 1 |
| What was Maya diagnosed with? | 2 | 1 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| What did I tell you about my health? | 1 | 1 | 3 (text ranking only) | 3 | 1 (catalog) | 1 |
| Where is Maya planning a trip? | 1 | 6 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| When is Maya's trip to Jamaica? | 1 | 2 | 3 (catalog) | 3 | 1 (catalog) | 1 |
| Is Maya expecting a baby? | 3 | 3 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| What life event did Maya mention? | 4 | 5 | 8 (catalog) | 8 | 3 (catalog) | 3 |
| What is my deductible? | 1 | 1 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| What did Maya's coworker Dana ask about? | 2 | 1 | 1 (catalog) | 1 | 1 (catalog) | 1 |

