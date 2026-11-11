# member-v1: `has` attributes

One meaning per attribute, used across the whole conversation.

- `deductible`: the individual deductible, as stated ("$1,500").
- `family_deductible`: the family deductible ("$3,000").
- `out_of_pocket_maximum`: the yearly limit ("$6,000").
- `coverage`: something the plan covers, in the turn's words ("travel vaccines", "Prenatal visits are covered in
  full in network", "chemotherapy", "preventive cleanings"). Negative polarity means the plan does not include it
  ("dental").
- `prior_authorization`: what needs approval before it is covered ("some infusions").
- `health_state`: a state of the member's health ("pregnant").
- `diagnosis`: a diagnosed condition ("breast cancer").
- `enrollment_window`: how long she has to change the plan ("30 days").
- `cleaning_frequency`: how often cleanings are covered ("twice a year").
- `trip_month`: the month named for a trip ("December").

The relations used are `partner_of`, `kin_of`, `involved_in` and `located_in`.
