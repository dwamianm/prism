# chat-v1: `has` attributes

One meaning per attribute, used across the whole conversation.

- `diet`: what Dana eats, as the turn names it ("vegetarian", later "pescatarian").
- `shift_pattern`: when a person works ("night shifts").
- `breed`: a dog's breed ("golden retriever", later "golden").
- `age`: a dog's age as stated ("3 year old").
- `race_count`: how many races a person has run ("ten half marathons").
- `run_frequency`: how often a person runs ("three runs a week").
- `long_run_distance`: the longest distance a person has run ("5 miles", later "10 miles").
- `race_result`: a finishing time ("1:58:30", "1:41").
- `drive_time`: how long a drive takes ("18 hours").
- `trip_month`: the month named for a trip ("July").

The race date is now the `time` of the race facts, not an attribute (round 2).

Everything else is a relation of the vocabulary: `partner_of`, `lives_in`, `owns`, `works_at`, `manager_of`,
`prefers`, `uses`, `involved_in`, `friend_of`, `kin_of`, `visits`, `allergic_to`.
