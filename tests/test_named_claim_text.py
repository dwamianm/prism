"""enable_named_claim_text: a claim from a turn whose I binds to a name is stored with the name in its text.

Every test runs on DuckDB and, with PRME_TEST_DATABASE_URL set, on PostgreSQL, and none calls a model: the
extractor answers from a table of recorded results, written as a hosted benefits assistant's chat with a member
whose name the application knows but who never introduces herself.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.named_text import NAMED_TEXT_KEY, has_first_person, name_first_person, named_claim_text, possessive
from prme.ingestion.reingest import derivation_settings
from prme.ingestion.schema import ExtractionResult
from prme.retrieval.packing import pack_context
from tests import test_durable_ingestion
from tests.test_ingest_names_and_claims import claims
from tests.test_owner_name_carry import Chat, entity_names, person, result, subjects

config = test_durable_ingestion.config
user = test_durable_ingestion.user

FAMILY = "OK. My husband is Theo and my child is Nico."
FAMILY_SENTENCE = "My husband is Theo and my child is Nico."
DIAGNOSIS = "I was just diagnosed with breast cancer? How does this plan cover it?"
DIAGNOSIS_SENTENCE = "I was just diagnosed with breast cancer?"
HOME = "I live in Denver with my partner Sam."
SISTER = "My sister Rachel lives in Portland."
INTRODUCTION = "Hi, I am Maya Lone Star. I work at Northwind."
THANKS = "Thanks, that helps."
ECHO = "Got it: your husband is Theo and your child is Nico."
JORDAN = "Jordan is allergic to shellfish."


def fact(subject, predicate, obj, quote, object_type="person"):
    return {"subject": subject, "predicate": predicate, "object": obj, "object_entity_type": object_type,
            "polarity": "positive", "evidence_quote": quote}


RESULTS = {
    FAMILY: ExtractionResult.model_validate({
        "entities": [person("Theo"), person("Nico")],
        "facts": [fact("I", "has_husband", "Theo", FAMILY_SENTENCE), fact("I", "has_child", "Nico", FAMILY_SENTENCE)],
    }),
    DIAGNOSIS: result([{"name": "breast cancer", "entity_type": "concept"}],
                      "I", "was_diagnosed_with", "breast cancer", DIAGNOSIS_SENTENCE, "concept"),
    HOME: result([{"name": "Denver", "entity_type": "location"}, person("Sam")], "I", "lives_in", "Denver", HOME,
                 "location"),
    SISTER: result([person("Rachel"), {"name": "Portland", "entity_type": "location"}],
                   "Rachel", "lives_in", "Portland", SISTER, "location"),
    INTRODUCTION: result([person("Maya Lone Star"), {"name": "Northwind", "entity_type": "organization"}],
                         "I", "works_at", "Northwind", "I work at Northwind.", "organization"),
    THANKS: ExtractionResult(),
    ECHO: ExtractionResult(),
    JORDAN: result([person("Jordan"), {"name": "shellfish", "entity_type": "concept"}],
                   "Jordan", "is_allergic_to", "shellfish", JORDAN, "concept"),
}


def named(base, **updates):
    """The configuration a hosted project runs, with the option on."""
    return base.model_copy(update={"enable_ingest_claim_fixes": True, "enable_owner_name_carry": True,
                                   "enable_named_claim_text": True, **updates})


class Member(Chat):
    def __init__(self, engine):
        super().__init__(engine)

        async def extract(content, *, role="user", **options):
            self.requests.append((content, role, options))
            return RESULTS[content]

        engine._pipeline._extraction_provider.extract = extract


def texts(found):
    return sorted(node.content for node, _ in found)


class TestRewrite:
    @pytest.mark.parametrize(("text", "expected"), [
        (FAMILY_SENTENCE, "Maya's husband is Theo and Maya's child is Nico."),
        (DIAGNOSIS_SENTENCE, "Maya was just diagnosed with breast cancer?"),
        ("I am planning a trip for us in December to Jamaica.", "Maya is planning a trip for us in December to Jamaica."),
        ("I'm pregnant.", "Maya is pregnant."),
        ("I've been vegetarian for six years.", "Maya has been vegetarian for six years."),
        ("I don't eat meat.", "Maya doesn't eat meat."),
        ("I just got the offer from Brightpath.", "Maya just got the offer from Brightpath."),
        ("On weekends I am learning Rust.", "On weekends Maya is learning Rust."),
        ("On weekends, I am learning Rust.", "On weekends, Maya is learning Rust."),
        ("Yes, I have two kids.", "Yes, Maya has two kids."),
        ("I went home and I am tired.", "Maya went home and Maya is tired."),
        ("I left and I was tired.", "Maya left and Maya was tired."),
        ("Jordan and I went to Kyoto.", "Jordan and Maya went to Kyoto."),
        ("Jordan and I will fly.", "Jordan and Maya will fly."),
        ("Jordan and I'll fly.", "Jordan and Maya will fly."),
        ("My partner Jordan and I are planning a trip.", "Maya's partner Jordan and Maya are planning a trip."),
        ("The report is mine.", "The report is Maya's."),
        ("Send it to me.", "Send it to Maya."),
        ("The plan covers it, i.e. my deductible applies.", "The plan covers it, i.e. Maya's deductible applies."),
        ("How do i enroll my newborn? I am pregnant.", "How do i enroll Maya's newborn? Maya is pregnant."),
        # Only the plain spellings are the pronoun, so an abbreviation that reads as one is left alone (#409).
        ("I have ME/CFS.", "Maya has ME/CFS."),
        ("Augusta, ME is cold.", "Augusta, ME is cold."),
        # A contraction keeps the apostrophe the user typed, and an all-caps verb keeps its emphasis.
        ("I can’t swim.", "Maya can’t swim."),
        ("I AM PREGNANT.", "Maya IS PREGNANT."),
        ("I'M PREGNANT.", "Maya IS PREGNANT."),
        # "mine" stays the possessive pronoun after a negation, an adverb, "of" or a coordinator.
        ("The book is not mine.", "The book is not Maya's."),
        ("It isn't mine.", "It isn't Maya's."),
        ("This is really mine.", "This is really Maya's."),
        ("The money is all mine.", "The money is all Maya's."),
        ("Mine is blue.", "Maya's is blue."),
        ("That's mine.", "That's Maya's."),
        ("A friend of mine called.", "A friend of Maya's called."),
        ("The blue book is hers and mine.", "The blue book is hers and Maya's."),
        # A proper noun in an adverbial phrase before I is a subject, not a title.
        ("In Denver I found a job.", "In Denver Maya found a job."),
        ("Last Monday I went home.", "Last Monday Maya went home."),
        ("Yesterday at Northwind I met Sam.", "Yesterday at Northwind Maya met Sam."),
        ("I told Mom I was tired.", "Maya told Mom Maya was tired."),
        # A lowercase common noun before I is a relative clause, not a title.
        ("The class I took was hard.", "The class Maya took was hard."),
        ("The book I bought was cheap.", "The book Maya bought was cheap."),
        # Only an interjection at the start of a clause makes "my god" a set phrase.
        ("Wow my new phone is great.", "Wow Maya's new phone is great."),
        ("Hey my ride is here.", "Hey Maya's ride is here."),
        ("Ah my head hurts.", "Ah Maya's head hurts."),
        ("Oh my phone is dead.", "Oh Maya's phone is dead."),
        ("I gave my word on it.", "Maya gave Maya's word on it."),
        # An all-caps contraction keeps its emphasis.
        ("I'VE BEEN THERE.", "Maya HAS BEEN THERE."),
        ("I’VE BEEN THERE.", "Maya HAS BEEN THERE."),
        ("That’s mine.", "That’s Maya's."),
        ("My 2 kids are here.", "Maya's 2 kids are here."),
        # A lowercase i is not the pronoun, so the words it is not part of are still rewritten.
        ("i think so.", "i think so."),
        ("i lost my keys.", "i lost Maya's keys."),
        ("Jordan is allergic to shellfish.", "Jordan is allergic to shellfish."),
    ])
    def test_the_first_person_becomes_the_name(self, text, expected):
        assert name_first_person(text, "Maya") == expected

    @pytest.mark.parametrize("text", [
        HOME, "I prefer trains to flights.", "I need a JR Pass.", "I'd like to book it.", "Do I need a card?",
        "I did it myself.", "I were there.", "Then I.",
        # A comma is not a coordinator, and after "and" a verb that changes after a name may start a clause.
        "Kai, I need help.", "Jordan and I have a dog.", "Jordan and I don't eat meat.",
        "My partner Jordan and I've been married for years.", "I left and I've got the keys.",
        # A quoted first person is someone else's speech, in double or single quotes (#409).
        'My husband said "I was fired."', 'He said “I’m tired.”',
        "My husband said 'call me later.'", "He said 'I lost my keys.'", "‘I was fired,’ my husband said.",
        "He said—'call me later.'", "He said:'call me later.'", "He said ’I lost my keys,’ and left.",
        'He said "she said "I lost my keys" yesterday."',
        # A set phrase is not a possessive, with or without a comma.
        "Oh my god, I won.", "Oh, my god, I won.", "Oh my, that is a lot.", "My, what a surprise.",
        "My god, I won.",
        # The noun "mine", the Roman numeral I, and verbs the table cannot tell from a past tense.
        "My uncle owns a coal mine.", "The mine is closed.",
        "World War I ended in 1918.", "Part I is long.", "Type I diabetes is common.",
        "Title I was amended.", "Schedule I was complex.", "Appendix I was updated.",
        "I lay the book down.",
        "I weed the garden.",
    ])
    def test_a_sentence_it_cannot_rewrite_safely_is_refused(self, text):
        assert name_first_person(text, "Maya") is None

    def test_a_long_text_is_rewritten_in_one_pass(self):
        """Caller-controlled text must stay linear: 20,000 first-person words do not multiply the work (#409 review)."""
        text = "my " * 20000 + "wallet."
        started = time.monotonic()
        assert name_first_person(text, "Maya") is not None
        assert time.monotonic() - started < 5.0

    @pytest.mark.parametrize("text", [
        HOME, 'My husband said "I was fired."', 'He said “I’m tired.”', "My husband said 'call me later.'",
        'He said "she said "I lost my keys" yesterday."',
        "Oh my god, I won.", "Oh, my god, I won.", "Oh my, that is a lot.",
        "My uncle owns a coal mine.", "World War I ended in 1918.", "Part I is long.",
        "I lay the book down.", "I weed the garden.",
    ])
    def test_the_label_keeps_a_sentence_the_rewrite_refuses(self, text):
        assert named_claim_text(text, "Maya") == f"Maya: {text}"

    def test_a_sentence_it_can_rewrite_is_not_labeled(self):
        assert named_claim_text(FAMILY_SENTENCE, "Maya") == "Maya's husband is Theo and Maya's child is Nico."

    @pytest.mark.parametrize("text", ["Jordan is here.", "Augusta, ME is cold.",
                                      "She dotted the I's and crossed the T's."])
    def test_a_text_with_no_first_person_is_left_as_it_is(self, text):
        assert named_claim_text(text, "Maya") == text

    def test_a_name_that_ends_in_s_takes_a_bare_apostrophe(self):
        assert possessive("James") == "James'" and possessive("Maya Lone Star") == "Maya Lone Star's"
        assert name_first_person("My child is Nico.", "James") == "James' child is Nico."

    def test_a_contraction_stays_whole(self):
        assert name_first_person("I'll call.", "Maya") == "Maya will call."
        assert name_first_person("I’m sure.", "Maya") == "Maya is sure."

    def test_has_first_person(self):
        assert has_first_person("my plan") and has_first_person("Do I?") and not has_first_person("i.e. the plan")
        assert not has_first_person("Jordan is here.") and not has_first_person("We chose Tokyo.")
        # An abbreviation and a lowercase i are not the pronoun (#409).
        assert has_first_person("I have ME/CFS") and not has_first_person("Augusta, ME is cold.")
        assert not has_first_person("i think so")


class TestSpeaker:
    """The caller names the member as the speaker of her turns, as a hosted application should."""

    async def test_the_claims_name_the_speaker_and_keep_the_evidence_verbatim(self, config, user):
        async with MemoryEngine.open(named(config)) as engine:
            chat = Member(engine)
            found = await chat.say(FAMILY, user, speaker="Maya")
            names = await entity_names(engine, user)
        assert texts(found) == ["Maya has child Nico.", "Maya's husband is Theo and Maya's child is Nico."]
        by_text = {node.content: node for node, _ in found}
        sentence = by_text["Maya's husband is Theo and Maya's child is Nico."]
        statement = by_text["Maya has child Nico."]
        # Grounding widens the evidence to the whole paragraph; it stays the user's words.
        assert sentence.metadata["evidence_quote"] == FAMILY
        assert sentence.metadata["grounding_method"] == "claim_sentences_v1"
        assert sentence.metadata[NAMED_TEXT_KEY] == {"name": "Maya", "original": FAMILY_SENTENCE}
        # The second claim of the sentence states itself, with the name; both record the sentence they came from.
        assert statement.metadata["grounding_method"] == "claim_statement_v1"
        assert statement.metadata[NAMED_TEXT_KEY] == {"name": "Maya", "original": FAMILY_SENTENCE}
        # The literal pronoun stays the subject, and the claims attach to the member's entity, not to an I.
        assert {node.metadata["subject"] for node, _ in found} == {"I"}
        assert {name for name, _, _ in subjects(found)} == {"Maya"}
        assert "I" not in names and names.count("Maya") == 1

    async def test_a_sentence_that_cannot_be_rewritten_is_kept_whole_under_the_name(self, config, user):
        async with MemoryEngine.open(named(config)) as engine:
            chat = Member(engine)
            [(node, subject)] = await chat.say(HOME, user, speaker="Maya")
        assert node.content == f"Maya: {HOME}"
        assert node.metadata["grounding_method"] == "claim_sentences_v1"
        assert node.metadata[NAMED_TEXT_KEY] == {"name": "Maya", "original": HOME}
        assert node.metadata["evidence_quote"] == HOME and subject.content == "Maya"

    async def test_a_question_mark_and_the_rest_of_the_sentence_stay(self, config, user):
        async with MemoryEngine.open(named(config)) as engine:
            chat = Member(engine)
            [(node, _)] = await chat.say(DIAGNOSIS, user, speaker="Maya")
        assert node.content == "Maya was just diagnosed with breast cancer?"

    async def test_a_claim_about_someone_else_still_names_the_speaker_for_its_my(self, config, user):
        async with MemoryEngine.open(named(config)) as engine:
            chat = Member(engine)
            [(node, subject)] = await chat.say(SISTER, user, speaker="Maya")
        assert node.content == "Maya's sister Rachel lives in Portland." and subject.content == "Rachel"
        assert "speaker_reference" not in node.metadata

    async def test_a_text_with_no_first_person_is_unchanged(self, config, user):
        async with MemoryEngine.open(named(config)) as engine:
            chat = Member(engine)
            [(node, _)] = await chat.say(JORDAN, user, speaker="Maya")
        assert node.content == JORDAN and NAMED_TEXT_KEY not in node.metadata

    async def test_the_extractor_is_asked_exactly_as_without_the_option(self, config, user):
        async with MemoryEngine.open(named(config)) as engine:
            chat = Member(engine)
            await chat.say(FAMILY, user, speaker="Maya")
        async with MemoryEngine.open(named(config, enable_named_claim_text=False)) as engine:
            plain = Member(engine)
            await plain.say(FAMILY, user, speaker="Maya")
        assert chat.requests == plain.requests


class TestCarriedName:
    """A later turn that names nobody takes the name an earlier user turn gave (enable_owner_name_carry)."""

    async def test_the_carried_name_is_written_into_the_text_and_the_statement(self, config, user):
        async with MemoryEngine.open(named(config)) as engine:
            chat = Member(engine)
            first = await chat.say(INTRODUCTION, user)
            later = await chat.say(FAMILY, user)
        # "I work" has no safe third-person form in the table, so the sentence is kept whole under the name.
        assert texts(first) == ["Maya Lone Star: I work at Northwind."]
        assert texts(later) == ["Maya Lone Star has child Nico.",
                                "Maya Lone Star's husband is Theo and Maya Lone Star's child is Nico."]
        assert {node.metadata["speaker_reference"]["basis"] for node, _ in later} == {"owner_name"}
        assert {name for name, _, _ in subjects(later)} == {"Maya Lone Star"}

    async def test_a_turn_whose_claims_keep_no_i_still_takes_the_name_for_its_my(self, config, user):
        async with MemoryEngine.open(named(config)) as engine:
            chat = Member(engine)
            await chat.say(INTRODUCTION, user)
            await chat.say(THANKS, user)
            [(node, subject)] = await chat.say(SISTER, user)
        assert node.content == "Maya Lone Star's sister Rachel lives in Portland."
        assert subject.content == "Rachel" and node.metadata[NAMED_TEXT_KEY]["name"] == "Maya Lone Star"

    async def test_without_the_option_the_carried_name_stays_out_of_the_text(self, config, user):
        async with MemoryEngine.open(named(config, enable_named_claim_text=False)) as engine:
            chat = Member(engine)
            await chat.say(INTRODUCTION, user)
            later = await chat.say(FAMILY, user)
        assert texts(later) == [FAMILY_SENTENCE, "The speaker has child Nico."]
        assert not any(NAMED_TEXT_KEY in node.metadata for node, _ in later)

    async def test_a_name_no_turn_gave_is_never_invented(self, config, user):
        async with MemoryEngine.open(named(config)) as engine:
            chat = Member(engine)
            found = await chat.say(FAMILY, user)
            names = await entity_names(engine, user)
        assert texts(found) == [FAMILY_SENTENCE, "The speaker has child Nico."]
        assert "I" in names


class TestBatchedExtraction:
    async def test_the_batched_path_a_hosted_project_runs_names_the_claims(self, config, user):
        async with MemoryEngine.open(named(config, extraction_batch_target_tokens=2048)) as engine:
            async def extract_batch(turns, **options):
                return {"turns": [{**RESULTS[turn.content].model_dump(mode="json"), "turn": turn.index}
                                  for turn in turns]}

            engine._pipeline._extraction_provider.extract_batch = extract_batch
            start = datetime(2026, 11, 2, 19, 30, tzinfo=timezone.utc)
            messages = [{"content": content, "role": role, "speaker": speaker,
                         "event_time": start + timedelta(minutes=index)}
                        for index, (content, role, speaker) in enumerate([
                            (FAMILY, "user", "Maya"), (ECHO, "assistant", None), (DIAGNOSIS, "user", "Maya")])]
            event_ids = await engine.ingest_batch(messages, user_id=user, session_id="s1", wait_for_extraction=True)
            family = await claims(engine, user, event_ids[0])
            diagnosis = await claims(engine, user, event_ids[2])
            names = await entity_names(engine, user)
        assert texts(family) == ["Maya has child Nico.", "Maya's husband is Theo and Maya's child is Nico."]
        assert texts(diagnosis) == ["Maya was just diagnosed with breast cancer?"]
        assert names.count("Maya") == 1 and "I" not in names


class TestPacking:
    def test_packing_spells_the_metadata_key_as_ingestion_does(self):
        from prme.retrieval import packing

        assert packing._NAMED_TEXT_KEY == NAMED_TEXT_KEY

    def test_a_named_claim_folds_into_the_turn_that_shows_its_sentence(self):
        """The reader context packs each source text once (packing.fold_repeated_text), the named claim included."""
        from tests.test_fold_repeated_text import lines, packed_ids, reader, record, turn

        sentence = "I'm a data analyst at Northwind, and my manager Priya keeps asking me for weekly dashboards."
        named_sentence = named_claim_text(sentence, "Dana")
        assert named_sentence == ("Dana is a data analyst at Northwind, and Dana's manager Priya keeps asking Dana "
                                  "for weekly dashboards.")
        claim = record(10, named_sentence, .8, metadata={NAMED_TEXT_KEY: {"name": "Dana", "original": sentence}})
        bundle = pack_context([turn(.9), claim], reader(fold_repeated_text=True))
        assert packed_ids(bundle) == {1} and len(lines(bundle)) == 1
        # Packed before its turn, the claim gives way to it as the sentence itself does.
        bundle = pack_context([turn(.7), claim], reader(fold_repeated_text=True))
        assert packed_ids(bundle) == {1}
        # A claim named from another sentence stays.
        other = record(11, "Dana quit coffee last month.", .6,
                       metadata={NAMED_TEXT_KEY: {"name": "Dana", "original": "I quit coffee last month."}})
        bundle = pack_context([turn(.9), claim, other], reader(fold_repeated_text=True))
        assert packed_ids(bundle) == {1, 11}

    @pytest.mark.parametrize("value", ["a caller's own value", ["a", "list"], 3, {"original": "a data analyst at Northwind"},
                                       {"name": "Priya", "original": "a data analyst at Northwind"}])
    def test_a_callers_own_metadata_under_the_key_is_not_read_as_a_named_claim(self, value):
        """The key is not reserved: a turn stored with any value under it packs as it did before the option."""
        from prme.models.speaker import SPEAKER_METADATA_KEY
        from prme.types import NodeType
        from tests.test_fold_repeated_text import SENTENCE, TURN, packed_ids, reader, record

        turn = record(1, TURN, .8, node_type=NodeType.NOTE,
                      metadata={SPEAKER_METADATA_KEY: "Dana", NAMED_TEXT_KEY: value})
        claim = record(10, SENTENCE, .9)
        for candidates in ([turn, claim], [claim, turn]):
            assert packed_ids(pack_context(candidates, reader(fold_repeated_text=True))) == {1}


class TestSettings:
    def test_the_option_is_off_by_default_and_a_dump_names_it(self):
        default = PRMEConfig(_env_file=None)
        assert default.enable_named_claim_text is False
        assert PRMEConfig(_env_file=None, enable_named_claim_text=True).model_dump(mode="json")[
            "enable_named_claim_text"] is True

    async def test_the_derivation_settings_name_it_only_when_it_is_on(self, config):
        async with MemoryEngine.open(named(config)) as engine:
            on = derivation_settings(engine._pipeline)
        async with MemoryEngine.open(named(config, enable_named_claim_text=False)) as engine:
            off = derivation_settings(engine._pipeline)
        assert on.pop("named_claim_text") is True and "named_claim_text" not in off and on == off
