"""Lab-wide search — the box an assessor types into.

Ryan asked for "sample searchability" as the headline, in the run-up to the
ISO/IEC 17025 PJLA assessment in September 2026. Read literally that is one
column (`lem_machine_log.lab_id`); read as the thing actually wanted, it is
"somebody types a word and finds the thing" — and the words a bench tech or an
assessor types are a Lab ID, an instrument, a method, a QC standard, a level,
or a person.

What these tests care about, in the order they cost most to get wrong:

* **Ranking is the product.** A search box that returns the right set in the
  wrong order is a search box nobody uses twice. So every ranking test here
  asserts the ORDER of the list, never mere membership — membership passes
  against a function that returns everything.
* **Every "should match" has a "must not match".** Substring matching is easy
  to make so loose it matches the whole lab, and a test suite that only ever
  asserts presence cannot see that happening.
* **The query is INPUT, never a pattern.** It arrives from a text box on a
  public page. `%`, `.*`, `[a-z]` and `(((((…` are four characters a user might
  type and must be four characters we look for, not four instructions we obey.
* **Nothing here may reach LabCore.** This is the load pattern CLAUDE.md's
  performance section exists to prevent: one read per keystroke per viewer is
  strictly worse than the 17-ops-per-refresh that started all this. The module
  takes rows the snapshot already holds and nothing else, and a tripwire below
  asserts it imports no way to ask.
* **A cap that is not reported is a lie.** "Showing 25" over 313 matches, drawn
  without saying so, reads to an assessor as "the lab has 25 of those".

The four blank-ish states are deliberately four, not two: nothing typed, not
enough typed, nothing found, and found. A UI that cannot tell "no results" from
"you have not typed anything yet" draws "No results" at rest.
"""
import lab_search
from lab_search import (STATE_IDLE, STATE_NO_MATCH, STATE_OK, STATE_SHORT,
                        search_rows, squash)


# ── the fixture lab ───────────────────────────────────────────────────────
#
# Small, and every row in it is doing a job: two near-identical instruments
# (so a tie has to break deterministically), one Lab ID run on BOTH of them (so
# a sample hit has to say which machine and when), a standard whose name mixes
# a word and a part number, and two levels with instruments standing on each.

def _machines():
    return [
        {"machine_uid": "pac-flash-1", "title": "PAC Flash 1",
         "level_uid": "lvl-ground", "status": "GREEN",
         "qc_targets": [{"sample": "Diesel - AO25", "test": "Flash Point"}],
         "effective_specs": [{"test_name": "Flash Point",
                              "sample_id": "Diesel - AO25"}]},
        {"machine_uid": "pac-flash-2", "title": "PAC Flash 2",
         "level_uid": "lvl-mezz", "status": "GREEN",
         "qc_targets": [{"sample": "Diesel - AO25", "test": "Flash Point"}],
         "effective_specs": [{"test_name": "Flash Point",
                              "sample_id": "Diesel - AO25"}]},
        {"machine_uid": "multitek-s", "title": "Multitek S",
         "level_uid": "lvl-ground", "status": "RED",
         "qc_targets": [],
         "effective_specs": [{"test_name": "Sulfur", "sample_id": "STD-1"}]},
        {"machine_uid": "optimpp-1", "title": "OptiMPP 1",
         "level_uid": "lvl-mezz", "status": "GREY",
         "qc_targets": [], "effective_specs": []},
    ]


def _levels():
    return [{"uid": "lvl-ground", "name": "Ground", "rank": 0},
            {"uid": "lvl-mezz", "name": "Mezzanine", "rank": 1}]


def _events():
    return [
        {"machine_uid": "pac-flash-2", "ts": "2026-08-26T09:12:00",
         "kind": "qc", "lab_id": "L-37712", "test_name": "Flash Point",
         "value": "61.5", "detail": ""},
        {"machine_uid": "pac-flash-1", "ts": "2026-08-25T14:03:00",
         "kind": "qc", "lab_id": "L-37712", "test_name": "Flash Point",
         "value": "60.9", "detail": ""},
        {"machine_uid": "multitek-s", "ts": "2026-08-26T08:40:00",
         "kind": "qc", "lab_id": "STD-1", "test_name": "Sulfur",
         "value": "0.42", "detail": ""},
        {"machine_uid": "optimpp-1", "ts": "2026-08-24T11:00:00",
         "kind": "config", "lab_id": "", "test_name": "", "value": "",
         "detail": ""},
    ]


def _find(answer, machines=None, events=None, levels=None, **kw):
    return search_rows(answer,
                       machines=_machines() if machines is None else machines,
                       events=_events() if events is None else events,
                       levels=_levels() if levels is None else levels, **kw)


def _labels(answer):
    return [hit["label"] for hit in answer["results"]]


def _kinds(answer):
    return [hit["kind"] for hit in answer["results"]]


def _only(answer):
    """The single result, insisted upon — so a loosened matcher fails here."""
    assert len(answer["results"]) == 1, _labels(answer)
    return answer["results"][0]


class TestTheFourStates:
    """Nothing typed, not enough typed, nothing found, found. A UI needs all
    four; collapsing any two draws the wrong sentence at rest."""

    def test_an_empty_query_is_idle_and_not_no_results(self):
        answer = _find("")
        assert answer["state"] == STATE_IDLE
        assert answer["results"] == []
        assert answer["matched"] == 0

    def test_whitespace_only_is_still_idle(self):
        assert _find("   \t \n ")["state"] == STATE_IDLE

    def test_one_character_is_short_not_a_capped_dump_of_the_lab(self):
        answer = _find("f")
        assert answer["state"] == STATE_SHORT
        assert answer["results"] == []

    def test_punctuation_only_is_short_because_nothing_survives_folding(self):
        # "--" is typed, so it is not idle; it carries no letters, so it is not
        # a search either. "Keep typing" is the honest answer to both.
        for typed in ("--", "%%", "  .  "):
            assert _find(typed)["state"] == STATE_SHORT, typed

    def test_a_query_matching_nothing_says_so_distinctly(self):
        answer = _find("zzzquux")
        assert answer["state"] == STATE_NO_MATCH
        assert answer["results"] == []
        assert answer["matched"] == 0

    def test_a_query_matching_something_is_ok(self):
        assert _find("flash")["state"] == STATE_OK

    def test_the_four_states_are_four_different_strings(self):
        assert len({STATE_IDLE, STATE_SHORT, STATE_NO_MATCH, STATE_OK}) == 4


class TestTheHeadlineCaseIsALabId:
    """`lem_machine_log.lab_id` is where the Lab IDs live and this is the
    search Ryan actually asked for."""

    def test_the_lab_id_as_written_finds_the_sample(self):
        hit = _only(_find("L-37712"))
        assert hit["kind"] == "sample"
        assert hit["label"] == "L-37712"

    def test_the_bare_number_finds_the_prefixed_lab_id(self):
        # Nobody says "dash three seven seven one two" out loud, and the number
        # is what is written on the bottle.
        hit = _only(_find("37712"))
        assert hit["kind"] == "sample"
        assert hit["label"] == "L-37712"

    def test_a_neighbouring_lab_id_does_not_match(self):
        # L-37713 shares five of six characters with L-37712 and is a
        # different sample. Fuzzy here would be a 17025 problem, not a UX one.
        assert _find("L-37713")["state"] == STATE_NO_MATCH

    def test_the_sample_hit_carries_where_and_when_not_just_a_label(self):
        hit = _only(_find("L-37712"))
        # The MOST RECENT run, because that is the one somebody is chasing.
        assert hit["machine_uid"] == "pac-flash-2"
        assert hit["machine_title"] == "PAC Flash 2"
        assert hit["level_uid"] == "lvl-mezz"
        assert hit["ts"] == "2026-08-26T09:12:00"
        assert hit["meta"]["test_name"] == "Flash Point"
        assert hit["meta"]["value"] == "61.5"

    def test_the_sample_hit_names_every_machine_that_ran_it(self):
        # The same ID measured on two units is the interesting case, and
        # answering with only one of them hides half the record.
        hit = _only(_find("L-37712"))
        assert hit["machine_count"] == 2
        assert [m["machine_uid"] for m in hit["machines"]] == [
            "pac-flash-2", "pac-flash-1"]

    def test_a_standard_run_as_a_sample_is_found_by_its_id(self):
        labels = _labels(_find("STD-1"))
        assert "STD-1" in labels


class TestPunctuationAndCaseAndSpacing:
    """`PAC Flash 2`, `pac-flash-2` and `pacflash2` are one instrument."""

    def test_every_spelling_of_the_same_instrument_reaches_it(self):
        for typed in ("PAC Flash 2", "pac-flash-2", "  pac   flash 2 ",
                      "PAC_FLASH_2", "pacflash2"):
            hit = _only(_find(typed))
            assert hit["kind"] == "equipment", typed
            assert hit["machine_uid"] == "pac-flash-2", typed
            assert hit["match"] == "exact", typed

    def test_flash2_finds_the_second_unit_and_only_the_second(self):
        # The stated case. It must NOT drag in PAC Flash 1 or Flash Point:
        # a matcher loose enough to return those is loose enough to return
        # the whole rail.
        hit = _only(_find("flash2"))
        assert hit["machine_uid"] == "pac-flash-2"

    def test_squash_is_the_one_normalisation_and_it_is_lossy_the_same_way(self):
        assert squash("PAC Flash 2") == squash("pac-flash-2") == "pacflash2"
        assert squash("Diesel - AO25") == "dieselao25"
        assert squash("   ") == ""


class TestEveryKindIsItsOwnKind:
    """One query, several kinds of answer, each labelled — otherwise the UI
    has to guess what it is drawing."""

    def test_equipment_is_found_by_uid_by_title_and_by_a_partial(self):
        assert _only(_find("multitek-s"))["machine_uid"] == "multitek-s"
        assert _only(_find("OptiMPP 1"))["machine_uid"] == "optimpp-1"
        assert "OptiMPP 1" in _labels(_find("optim"))

    def test_a_method_is_a_method_and_lists_the_instruments_running_it(self):
        hit = _only(_find("Flash Point"))
        assert hit["kind"] == "method"
        assert hit["label"] == "Flash Point"
        assert [m["machine_uid"] for m in hit["machines"]] == [
            "pac-flash-1", "pac-flash-2"]

    def test_a_qc_standard_is_found_by_its_part_number(self):
        hit = _only(_find("AO25"))
        assert hit["kind"] == "standard"
        assert hit["label"] == "Diesel - AO25"
        assert hit["machine_count"] == 2

    def test_a_level_is_found_and_says_what_stands_on_it(self):
        hit = _only(_find("Mezzanine"))
        assert hit["kind"] == "level"
        assert hit["level_uid"] == "lvl-mezz"
        assert sorted(m["machine_uid"] for m in hit["machines"]) == [
            "optimpp-1", "pac-flash-2"]

    def test_sulfur_does_not_reach_the_flash_units(self):
        uids = {hit.get("machine_uid") for hit in _find("Sulfur")["results"]}
        assert "pac-flash-1" not in uids and "pac-flash-2" not in uids

    def test_flash_does_not_reach_the_instruments_that_have_no_flash(self):
        labels = _labels(_find("flash"))
        assert "Multitek S" not in labels
        assert "OptiMPP 1" not in labels
        assert "Ground" not in labels and "Mezzanine" not in labels

    def test_mezzanine_does_not_return_the_other_level(self):
        assert "Ground" not in _labels(_find("Mezzanine"))


class TestNothingSuppliedIsNotAnError:
    """The route may be called before the snapshot has ever been built, and a
    half-filled row is what a partially-migrated table looks like."""

    def test_no_rows_at_all_is_a_clean_no_match(self):
        answer = search_rows("flash")
        assert answer["state"] == STATE_NO_MATCH
        assert answer["results"] == []

    def test_none_for_every_source_behaves_like_empty(self):
        assert search_rows("flash", machines=None, events=None,
                           levels=None)["state"] == STATE_NO_MATCH

    def test_rows_missing_every_key_are_skipped_not_fatal(self):
        answer = _find("flash", machines=[{}, {"title": "PAC Flash 9"}],
                       events=[{}], levels=[{}])
        assert answer["state"] == STATE_OK
        assert _labels(answer) == ["PAC Flash 9"]

    def test_a_row_that_is_not_a_dict_is_ignored(self):
        assert search_rows("flash", machines=["nonsense", None, 7],
                           events=[None, 3], levels=["x"],
                           )["state"] == STATE_NO_MATCH


# ══════════════════════════════════════════════════════════════════════════
# Ranking. Every test below asserts an ORDER. A ranking test that asserts
# membership passes against a function that returns everything, which is
# exactly the hollow shape this suite has been burned by before.
# ══════════════════════════════════════════════════════════════════════════

def _tier_lab():
    """Four instruments that match `sulfur` four different ways, and nothing
    else in the lab at all — so the list that comes back IS the ranking."""
    return [
        {"machine_uid": "desulfurisation-rig", "title": "Desulfurisation Rig"},
        {"machine_uid": "multitek-sulfur", "title": "Multitek Sulfur"},
        {"machine_uid": "sulfur-analyser", "title": "Sulfur Analyser"},
        {"machine_uid": "sulfur", "title": "Sulfur"},
    ]


class TestHowWellItMatchedBeatsWhatItMatched:

    def test_exact_then_prefix_then_word_then_substring_in_that_order(self):
        # Deliberately fed in the WRONG order, so passing means the ranking
        # ran and not that the input happened to be sorted.
        answer = search_rows("sulfur", machines=_tier_lab())
        assert _labels(answer) == ["Sulfur", "Sulfur Analyser",
                                   "Multitek Sulfur", "Desulfurisation Rig"]
        assert [hit["match"] for hit in answer["results"]] == [
            "exact", "prefix", "word", "substring"]

    def test_the_scores_actually_descend_and_never_tie_across_tiers(self):
        scores = [hit["score"]
                  for hit in search_rows("sulfur",
                                         machines=_tier_lab())["results"]]
        assert scores == sorted(scores, reverse=True)
        assert len(set(scores)) == 4

    def test_an_exact_id_outranks_a_fuzzy_title(self):
        # The stated requirement, with the two things that collide in real
        # data: a Lab ID and an instrument named after it.
        answer = search_rows(
            "STD-1",
            machines=[{"machine_uid": "std-1-verifier",
                       "title": "STD-1 Verifier"}],
            events=[{"machine_uid": "multitek-s", "ts": "2026-08-26T08:40:00",
                     "kind": "qc", "lab_id": "STD-1", "test_name": "Sulfur",
                     "value": "0.42"}])
        assert _kinds(answer) == ["sample", "equipment"]
        assert _labels(answer) == ["STD-1", "STD-1 Verifier"]

    def test_a_whole_word_prefix_beats_a_word_buried_in_a_name(self):
        answer = _find("flash")
        assert _labels(answer) == ["Flash Point", "PAC Flash 1", "PAC Flash 2"]
        assert _kinds(answer) == ["method", "equipment", "equipment"]

    def test_a_dead_tie_breaks_by_name_not_by_input_order(self):
        # The two Flash units are identical in length and tier, so their scores
        # are equal and something has to decide. Whatever decides has to be
        # stable, or the list reorders under the reader's cursor — the same
        # churn `build_machines` sorts by title to avoid.
        forwards = search_rows("flash", machines=_machines())
        backwards = search_rows("flash", machines=list(reversed(_machines())))
        units = [hit for hit in forwards["results"]
                 if hit["kind"] == "equipment"]
        assert units[0]["score"] == units[1]["score"]
        assert _labels(forwards) == _labels(backwards)
        assert [hit["label"] for hit in units] == ["PAC Flash 1", "PAC Flash 2"]

    def test_coverage_prefers_the_name_the_query_nearly_fills(self):
        answer = search_rows("flash", machines=[
            {"machine_uid": "rig-a", "title": "Flash Point Autosampler Rig"},
            {"machine_uid": "rig-b", "title": "Flash Rig"}])
        assert _labels(answer) == ["Flash Rig", "Flash Point Autosampler Rig"]

    def test_the_three_score_bands_cannot_overlap(self):
        # This is the ranking rule stated as arithmetic. If somebody widens a
        # kind weight or adds a sixth tier, this is what tells them the
        # guarantee "an exact match always outranks a fuzzy one" just broke.
        tiers = sorted(lab_search.TIER_SCORE.values())
        smallest_tier_gap = min(b - a for a, b in zip(tiers, tiers[1:]))
        heaviest_kind = max(lab_search.KIND_WEIGHT.values())
        assert smallest_tier_gap > heaviest_kind + lab_search.COVERAGE_MAX

        kinds = sorted(lab_search.KIND_WEIGHT.values())
        smallest_kind_gap = min(b - a for a, b in zip(kinds, kinds[1:]))
        assert smallest_kind_gap > lab_search.COVERAGE_MAX

    def test_every_kind_has_a_weight_and_no_two_share_one(self):
        assert set(lab_search.KIND_WEIGHT) == set(lab_search.KINDS)
        assert len(set(lab_search.KIND_WEIGHT.values())) == len(lab_search.KINDS)


# ══════════════════════════════════════════════════════════════════════════
# The caps. All of them reported; none of them silent.
# ══════════════════════════════════════════════════════════════════════════

def _busy_lab_events():
    """Thirty Lab IDs that all begin `FLASH`, newest last in the numbering."""
    return [{"machine_uid": "pac-flash-2",
             "ts": "2026-08-%02dT09:00:00" % (n if n > 9 else n),
             "kind": "qc", "lab_id": "FLASH-%03d" % n,
             "test_name": "Flash Point", "value": "61.0"}
            for n in range(1, 31)]


class TestTheCapIsVisible:

    def test_a_flooded_kind_reports_matched_and_shown_separately(self):
        answer = _find("flash", events=_busy_lab_events())
        assert answer["counts"]["sample"] == {"matched": 30, "shown": 10}
        assert answer["matched"] == 33          # 30 samples, 1 method, 2 units
        assert answer["shown"] == 13
        assert answer["truncated"] is True

    def test_no_single_kind_can_swallow_the_page(self):
        answer = _find("flash", events=_busy_lab_events())
        assert answer["counts"]["sample"]["shown"] == lab_search.PER_KIND_LIMIT

    def test_the_one_matching_instrument_survives_thirty_matching_samples(self):
        # The expensive wrong answer. "PAC Flash 2 is not in this lab" is far
        # worse than "there were more samples than fitted".
        answer = _find("flash", events=_busy_lab_events())
        assert "PAC Flash 2" in _labels(answer)
        assert "Flash Point" in _labels(answer)

    def test_a_limit_of_three_returns_the_best_of_each_kind_in_rank_order(self):
        answer = _find("flash", events=_busy_lab_events(), limit=3)
        assert _kinds(answer) == ["sample", "method", "equipment"]
        assert answer["shown"] == 3
        assert answer["matched"] == 33
        assert answer["truncated"] is True

    def test_the_reserved_sample_is_the_most_recent_one(self):
        answer = _find("flash", events=_busy_lab_events(), limit=3)
        assert answer["results"][0]["label"] == "FLASH-030"

    def test_samples_of_equal_score_come_back_newest_first(self):
        answer = _find("flash", events=_busy_lab_events())
        samples = [hit["label"] for hit in answer["results"]
                   if hit["kind"] == "sample"]
        assert samples[:3] == ["FLASH-030", "FLASH-029", "FLASH-028"]
        assert len(set(hit["score"] for hit in answer["results"]
                       if hit["kind"] == "sample")) == 1

    def test_an_uncapped_answer_says_so(self):
        answer = _find("flash")
        assert answer["truncated"] is False
        assert answer["matched"] == answer["shown"] == 3

    def test_a_hit_never_lists_more_machines_than_the_cap_but_counts_them_all(self):
        fleet = [{"machine_uid": "rig-%02d" % n, "title": "Rig %02d" % n,
                  "effective_specs": [{"test_name": "Flash Point",
                                       "sample_id": "Diesel - AO25"}]}
                 for n in range(12)]
        hit = _only(search_rows("Flash Point", machines=fleet))
        assert hit["machine_count"] == 12
        assert len(hit["machines"]) == lab_search.MAX_HIT_MACHINES
        assert hit["machines_truncated"] is True

    def test_the_limit_is_clamped_and_reported_never_obeyed_blindly(self):
        assert _find("flash", limit=0)["limit"] == 1
        assert _find("flash", limit=10 ** 9)["limit"] == lab_search.MAX_LIMIT
        assert _find("flash", limit=10 ** 9)["limit"] == 200
        assert _find("flash", limit="nonsense")["limit"] == lab_search.DEFAULT_LIMIT
        assert _find("flash")["limit"] == lab_search.DEFAULT_LIMIT

    def test_a_result_list_is_never_longer_than_the_limit(self):
        answer = _find("flash", events=_busy_lab_events(), limit=5)
        assert len(answer["results"]) <= 5

    def test_an_over_long_query_is_cut_and_says_it_was_cut(self):
        answer = _find("a" * 400)
        assert answer["query_truncated"] is True
        assert len(answer["query"]) == lab_search.MAX_QUERY_CHARS

    def test_a_query_that_fits_is_not_flagged(self):
        assert _find("flash")["query_truncated"] is False


class TestOneShapeInEveryState:
    """`/api/machines` answers warming and ready with the same keys so the page
    reads one shape. Four states here get the same treatment."""

    def test_every_state_answers_with_the_same_keys(self):
        shapes = [set(_find(typed)) for typed in
                  ("", "f", "zzzquux", "flash")]
        assert shapes[0] == shapes[1] == shapes[2] == shapes[3]

    def test_the_blank_states_carry_zeroed_counters_not_missing_ones(self):
        for typed in ("", "f", "zzzquux"):
            answer = _find(typed)
            assert answer["matched"] == 0 and answer["shown"] == 0
            assert answer["truncated"] is False
            assert answer["counts"] == {} and answer["kinds"] == []


# ══════════════════════════════════════════════════════════════════════════
# The operator column that is being added underneath us.
# ══════════════════════════════════════════════════════════════════════════

class TestOperatorsAreReadDefensively:

    def test_no_operator_anywhere_is_silence_not_an_error(self):
        answer = _find("Ryan")
        assert answer["state"] == STATE_NO_MATCH
        assert "operator" not in answer["counts"]

    def test_an_operator_column_is_used_the_moment_it_appears(self):
        events = _events()
        events[0]["operator"] = "Ryan Wilson"
        hit = _only(_find("Ryan", events=events))
        assert hit["kind"] == "operator"
        assert hit["label"] == "Ryan Wilson"
        assert hit["machine_uid"] == "pac-flash-2"
        assert hit["ts"] == "2026-08-26T09:12:00"

    def test_a_name_already_in_the_detail_json_is_found(self):
        # levels.py already writes {"by": …} into `detail` for move logs, so
        # some names are there today with no column at all.
        events = _events()
        events[3]["detail"] = '{"action": "move", "by": "Dana Reyes"}'
        hit = _only(_find("dana", events=events))
        assert hit["kind"] == "operator" and hit["label"] == "Dana Reyes"

    def test_the_same_person_on_two_benches_is_one_result(self):
        events = _events()
        events[0]["operator"] = "Ryan Wilson"
        events[1]["operator"] = "Ryan Wilson"
        hit = _only(_find("Ryan", events=events))
        assert hit["machine_count"] == 2

    def test_junk_in_the_operator_slot_never_reaches_the_answer(self):
        # Every one of these is a shape a half-migrated column really produces.
        for junk in (None, "", "   ", [], {}, 0):
            events = _events()
            events[0]["operator"] = junk
            answer = _find("Ryan", events=events)
            assert answer["state"] == STATE_NO_MATCH, junk

    def test_unparseable_detail_is_ignored_not_fatal(self):
        for junk in ("{not json at all", "{", "[]", '{"by": 17}',
                     '{"by": null}', "plain english note"):
            events = _events()
            events[3]["detail"] = junk
            assert _find("Ryan", events=events)["state"] == STATE_NO_MATCH, junk

    def test_an_operator_never_gets_dragged_into_an_unrelated_search(self):
        events = _events()
        events[0]["operator"] = "Ryan Wilson"
        assert "operator" not in _find("flash", events=events)["counts"]


# ══════════════════════════════════════════════════════════════════════════
# The query is input. It is never a pattern, and it can never cost more than
# a bounded scan.
# ══════════════════════════════════════════════════════════════════════════

class TestTheQueryIsNeverAPattern:

    def test_a_sql_wildcard_is_a_character_we_look_for(self):
        # `%` matches everything in LIKE. Here it folds away, and what is left
        # has to match on its own merits — `fla%h` is `flah`, which is nothing.
        assert _find("fla%h")["state"] == STATE_NO_MATCH
        assert _find("%")["state"] == STATE_SHORT

    def test_a_regex_wildcard_is_a_character_we_look_for(self):
        assert _find("fla.h")["state"] == STATE_NO_MATCH
        assert _find(".*")["state"] == STATE_SHORT
        assert _find("^flash$")["state"] == STATE_OK  # the punctuation folds off

    def test_a_character_class_matches_no_class(self):
        # If `[a-z]` were honoured this would return the whole lab.
        assert _find("[a-z]")["state"] == STATE_NO_MATCH

    def test_a_trailing_star_changes_nothing(self):
        plain, starred = _find("flash"), _find("flash*")
        assert _labels(plain) == _labels(starred)
        assert [h["score"] for h in plain["results"]] == [
            h["score"] for h in starred["results"]]

    def test_a_catastrophic_pattern_is_just_a_long_dull_string(self):
        import time
        started = time.monotonic()
        for evil in ("(" * 5000, "(a+)+$" * 500, "a" * 5000,
                     "\\" * 500, "*" * 5000, "(?:" * 900):
            answer = _find(evil, events=_busy_lab_events())
            assert answer["state"] in (STATE_SHORT, STATE_NO_MATCH), evil
        assert time.monotonic() - started < 2.0

    def test_a_big_index_is_still_one_bounded_pass(self):
        import time
        events = [{"machine_uid": "pac-flash-2",
                   "ts": "2026-08-26T09:00:00", "kind": "qc",
                   "lab_id": "L-%06d" % n, "test_name": "Flash Point",
                   "value": "1"} for n in range(5000)]
        index = lab_search.build_index(machines=_machines(), events=events,
                                       levels=_levels())
        started = time.monotonic()
        answer = lab_search.search("L-004321", index)
        assert time.monotonic() - started < 2.0
        assert answer["searched"] == len(index)
        assert len(answer["results"]) <= answer["limit"]
        assert answer["results"][0]["label"] == "L-004321"


class TestBuildOnceSearchOften:
    """The split that makes a per-keystroke box affordable: folding is paid on
    the snapshot's cycle, matching is paid per keystroke."""

    def test_an_index_can_be_searched_repeatedly_with_the_same_answer(self):
        index = lab_search.build_index(machines=_machines(),
                                       events=_events(), levels=_levels())
        first = lab_search.search("flash", index)
        second = lab_search.search("flash", index)
        assert first == second
        assert len(index) == 12

    def test_searching_does_not_mutate_the_index(self):
        index = lab_search.build_index(machines=_machines(),
                                       events=_events(), levels=_levels())
        before = len(index)
        answer = lab_search.search("Flash Point", index)
        answer["results"][0]["machines"].clear()
        answer["results"][0]["meta"]["test_name"] = "TAMPERED"
        again = lab_search.search("Flash Point", index)
        assert len(index) == before
        assert len(again["results"][0]["machines"]) == 2
        assert again["results"][0]["meta"] == {}

    def test_searching_with_no_index_at_all_is_a_clean_no_match(self):
        assert lab_search.search("flash", None)["state"] == STATE_NO_MATCH
        assert lab_search.search("flash")["state"] == STATE_NO_MATCH


# ══════════════════════════════════════════════════════════════════════════
# Tripwires. Declared-and-inert and working look identical from the outside;
# so do "cannot reach LabCore" and "does not happen to today".
# ══════════════════════════════════════════════════════════════════════════

def _source() -> str:
    import inspect
    return inspect.getsource(lab_search)


def _strip_prose(source: str) -> str:
    """`source` with comments and docstrings removed, and NOTHING else moved.

    The tripwires below assert that this module does not import `re`, does not
    reach LabCore and compiles no pattern. Run against the raw source they
    failed on the module's own PROSE: the docstring explaining *why* there is no
    `re` here contains the string "re", and the comment explaining why it cannot
    reach LabCore contains "LabCore". A guard that a file cannot document itself
    past is a guard people delete, and it fails without anything being wrong.

    So the check reads the code and only the code. Comments are located by
    `tokenize` and cut at their exact column, so a `#` inside a string is not
    one; docstrings are dropped through the AST, because a docstring is a plain
    string expression and no lexer can tell it from a string that matters.

    **The surviving code is byte-for-byte the source.** This used to rebuild
    the text by joining tokens with spaces, which turned `re.compile(x)` into
    `re . compile ( x )` — so five of the nine banned strings ("re.compile",
    "re.search", "re.match", "eval(", "exec(") could not appear in what was
    being searched no matter what the module did. A tripwire that cannot fire
    is worse than no tripwire: it is a green light nobody re-checks.
    """
    import ast
    import io
    import tokenize

    tree = ast.parse(source)
    doc_lines = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            doc_lines.update(range(first.lineno, (first.end_lineno or
                                                  first.lineno) + 1))

    cuts = {}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            cuts[row] = min(cuts.get(row, col), col)

    kept = []
    for number, line in enumerate(source.splitlines(), 1):
        if number in doc_lines:
            continue
        if number in cuts:
            line = line[:cuts[number]]
        kept.append(line)
    return "\n".join(kept)


def _code_only() -> str:
    return _strip_prose(_source())


class TestItCannotBecomeTheThingWeBannedThreeTimes:

    def test_the_module_compiles_no_pattern_from_anything(self):
        source = _code_only()
        for banned in ("import re", "re.compile", "re.search", "re.match",
                       "fnmatch", "eval(", "exec(", "LIKE ", "GLOB "):
            assert banned not in source, banned

    def test_the_module_imports_nothing_outside_the_standard_library(self):
        import ast
        allowed = {"__future__", "json", "typing", "unicodedata"}
        names = set()
        for node in ast.walk(ast.parse(_source())):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add((node.module or "").split(".")[0])
        assert names <= allowed, names - allowed

    def test_the_module_has_no_way_to_ask_labcore_anything(self):
        # The reviewer's check, made mechanical. One LabCore read per keystroke
        # per viewer is strictly worse than the 17-ops-per-refresh that the
        # snapshot service was built to end.
        source = _code_only().lower()
        for banned in ("labcore", "gateway", "snapshot_service", "flask",
                       "requests", "sqlite", "read_sql", "socket", "urllib"):
            assert banned not in source, banned

    def test_the_public_surface_is_the_three_functions_and_the_constants(self):
        for name in ("search", "search_rows", "build_index", "squash",
                     "SearchIndex", "KINDS", "DEFAULT_LIMIT", "MAX_LIMIT",
                     "PER_KIND_LIMIT", "MIN_QUERY_CHARS", "MAX_QUERY_CHARS",
                     "MAX_HIT_MACHINES"):
            assert hasattr(lab_search, name), name


class TestEveryResultCanBeNavigatedTo:

    def test_every_hit_of_every_kind_carries_the_full_envelope(self):
        events = _events()
        events[0]["operator"] = "Ryan Wilson"
        answer = search_rows("s", machines=_machines(), events=events,
                             levels=_levels(), limit=lab_search.MAX_LIMIT)
        assert answer["state"] == STATE_SHORT   # one character is still short
        seen = set()
        for typed in ("STD-1", "PAC Flash 2", "Diesel - AO25", "Flash Point",
                      "Mezzanine", "Ryan Wilson"):
            for hit in _find(typed, events=events)["results"]:
                seen.add(hit["kind"])
                assert set(hit) == {
                    "kind", "key", "id", "label", "score", "match", "field",
                    "machine_uid", "machine_title", "level_uid", "ts",
                    "machines", "machine_count", "machines_truncated", "meta"}
                assert hit["id"] and hit["label"] and hit["key"]
                for entry in hit["machines"]:
                    assert set(entry) == {"machine_uid", "title", "level_uid"}
        assert seen == set(lab_search.KINDS)


# ══════════════════════════════════════════════════════════════════════════
# A token has to carry signal. The tokens tier is a claim that the query is
# SEVERAL WORDS and that each of them starts a word here; a one-character
# token starts nearly every word in the lab, so on its own it is a wildcard
# wearing a word's clothes. Measured on a 60-instrument / 8000-row lab before
# this section existed: `l l` came back OK, at the `tokens` tier, with 99.1%
# of the index — outranking every honest substring match in the answer.
# ══════════════════════════════════════════════════════════════════════════

def _big_lab():
    """Sixty instruments and eight thousand log rows — the shape the reviewer
    measured on, small enough to build in a test."""
    titles = ["PAC Flash %d", "Multitek NS %d", "OptiMPP %d",
              "Desulfurisation Rig %d", "Koehler Cloud %d", "Anton Paar %d"]
    methods = ["Flash Point", "Sulfur", "Cloud Point", "Density", "Pour Point"]
    standards = ["Diesel - AO25", "STD-1", "Gasoline - BX11"]
    machines = [
        {"machine_uid": "m-%02d" % n,
         "title": titles[n % len(titles)] % (n // len(titles) + 1),
         "level_uid": "lvl-%d" % (n % 3), "status": "GREEN",
         "effective_specs": [{"test_name": methods[n % len(methods)],
                              "sample_id": standards[n % len(standards)]}]}
        for n in range(60)]
    events = [
        {"machine_uid": "m-%02d" % (n % 60),
         "ts": "2026-08-%02dT%02d:%02d:00" % (n % 28 + 1, n % 24, n % 60),
         "kind": "qc", "lab_id": "L-%06d" % n,
         "test_name": methods[n % len(methods)], "value": "%.2f" % (n % 100),
         "operator": ["Ryan Wilson", "Dana Reyes", "Lee Park"][n % 3]}
        for n in range(8000)]
    levels = [{"uid": "lvl-%d" % i, "name": ["Ground", "Mezzanine", "Loft"][i],
               "rank": i} for i in range(3)]
    return machines, events, levels


def _share_of_index(query):
    """What fraction of everything indexed a query claims to have matched."""
    machines, events, levels = _big_lab()
    index = lab_search.build_index(machines=machines, events=events,
                                   levels=levels)
    answer = lab_search.search(query, index)
    return answer, 100.0 * answer["matched"] / max(1, len(index))


class TestAOneCharacterTokenIsNotAWord:
    """Twelve characters, none of them a word, must not answer with a named QC
    standard at a tier that outranks every honest match on the page."""

    # Two halves, and they fail for different reasons. The first is one
    # letter typed twice; the second is two DIFFERENT letters, which no
    # deduplication can help with and which is where `l 0` lives.
    GIBBERISH = ("l l", "a a", "%a%a%", "(a+)+$(a+)+$", "a a a a a",
                 "l-l", "a+a", "0 0 0",
                 "0 l", "a d", "%a%d%", "(a+)+$(d+)+$", "a+d", "d a n")

    def test_gibberish_never_reaches_the_tokens_tier(self):
        for typed in self.GIBBERISH:
            answer = _find(typed, events=_busy_lab_events())
            tiers = {hit["match"] for hit in answer["results"]}
            assert "tokens" not in tiers, (typed, _labels(answer))

    def test_a_repeated_letter_is_one_word_typed_twice_not_two_words(self):
        # `l l` is not "two words that each start a word here" — it is one
        # letter, typed twice. The tokens tier is for `pac 2` and `flash pac`.
        answer = _find("l l", events=_busy_lab_events())
        assert answer["state"] == STATE_NO_MATCH, _labels(answer)

    def test_a_wall_of_punctuation_does_not_name_a_qc_standard(self):
        # The reported blocker, verbatim: 12 characters, 2 tokens, and an
        # answer of "Diesel - AO25" at the `tokens` tier.
        for typed in ("(a+)+$(a+)+$", "%a%a%", "a a"):
            answer = _find(typed)
            assert "Diesel - AO25" not in _labels(answer), typed
            assert answer["state"] == STATE_NO_MATCH, typed

    def test_a_single_letter_token_must_BE_a_word_not_merely_begin_one(self):
        # `l 37712` is a person typing a Lab ID with a space in it: `l` IS a
        # word of `L-37712`. `a 37712` is not — no word of it is `a`.
        assert _only(_find("l 37712"))["label"] == "L-37712"
        assert _find("a 37712")["state"] == STATE_NO_MATCH

    def test_two_different_letters_are_still_two_wildcards(self):
        # The half deduplication cannot reach. In a lab that numbers Lab IDs
        # `L-000nnn`, `0 l` starts BOTH words of nearly every one of them —
        # `0` starts `000839` and `l` starts `l` — and it is not the prefix or
        # the substring of any of them, so the tokens tier is the ONLY thing
        # that can answer with it. It did: 99.1% of the index, scored above
        # every honest match on the page. `l` is a whole word of `L-000839`
        # and stays legal; `0` is not, and that is the one that has to fail.
        answer, share = _share_of_index("0 l")
        assert answer["state"] == STATE_NO_MATCH, answer["matched"]
        assert share == 0.0
        second, share = _share_of_index("a d")
        assert second["state"] == STATE_NO_MATCH, second["matched"]
        assert share == 0.0

    def test_the_short_real_queries_still_find_their_instrument(self):
        fleet = [{"machine_uid": "pac-flash-2", "title": "PAC Flash 2"},
                 {"machine_uid": "gc-bench-2", "title": "GC Bench 2"},
                 {"machine_uid": "l-1-rig", "title": "L-1 Rig"}]
        assert _only(search_rows("PAC 2", machines=fleet))["machine_uid"] == \
            "pac-flash-2"
        assert _only(search_rows("GC 2", machines=fleet))["machine_uid"] == \
            "gc-bench-2"
        assert _only(search_rows("L-1", machines=fleet))["machine_uid"] == \
            "l-1-rig"

    def test_a_two_letter_method_name_is_still_findable(self):
        # A method named `pH` is two characters and a legitimate search.
        answer = search_rows("pH", machines=[
            {"machine_uid": "ph-meter", "title": "pH Meter",
             "effective_specs": [{"test_name": "pH"}]}])
        assert _labels(answer) == ["pH", "pH Meter"]
        assert answer["results"][0]["kind"] == "method"
        assert answer["results"][0]["match"] == "exact"

    def test_a_two_character_word_may_still_be_a_mere_PREFIX(self):
        # The floor is on the WORD, not on the query: `fl` is two characters
        # and is allowed to prefix `flash`. Only a one-character word — the
        # one that prefixes nearly everything — has to be exact. Tightening
        # this to three would take `fl point` and `GC 2` with it.
        hit = _only(_find("fl point"))
        assert hit["label"] == "Flash Point" and hit["match"] == "tokens"

    def test_the_tokens_tier_still_finds_words_out_of_order(self):
        # What the tier is FOR. Removing it entirely would also pass every
        # test above, and this is what says that would be wrong.
        hit = _only(_find("point flash"))
        assert hit["label"] == "Flash Point" and hit["match"] == "tokens"

    def test_gibberish_claims_a_negligible_share_of_a_real_lab(self):
        for typed in ("l l", "a a", "(a+)+$(a+)+$"):
            answer, share = _share_of_index(typed)
            assert share < 1.0, (typed, answer["matched"], share)

    def test_a_short_numeric_query_is_broad_but_it_says_so(self):
        # `00` is ONE token and a genuine prefix of a word in nearly every Lab
        # ID in a lab that numbers them `L-0000nn`, so it is not gibberish and
        # this does not pretend otherwise. What it must never do is claim a
        # narrow answer: the true total is reported beside the shown one.
        answer, share = _share_of_index("00")
        assert answer["truncated"] is True
        assert answer["matched"] > answer["shown"]
        assert answer["counts"]["sample"]["matched"] == answer["matched"] - \
            sum(answer["counts"][k]["matched"] for k in answer["counts"]
                if k != "sample")


# ══════════════════════════════════════════════════════════════════════════
# The cap decides MEMBERSHIP, and it has to keep the best of each kind — not
# the alphabetically first. Nothing in the suite saw this: the reviewer
# deleted `_assemble`'s pre-pick sort entirely and all 70 tests passed.
# ══════════════════════════════════════════════════════════════════════════

def _flash_lab():
    """Four hits on `flash`, one per tier, deliberately laid out so that the
    alphabetically-first of each kind is the WORST of that kind.

        FLASH             sample,    exact      — a Lab ID that IS the query
        Flash Rig         equipment, prefix
        XX-FLASHBACK-9    sample,    word
        Aflash Decanter   equipment, substring

    `Aflash Decanter` sorts before `Flash Rig`, and `XX-FLASHBACK-9` is the
    more recent of the two samples — so both of the reserved slots go to the
    wrong hit unless the reservation reads the score.
    """
    machines = [
        {"machine_uid": "flash-rig", "title": "Flash Rig"},
        {"machine_uid": "aflash-decanter", "title": "Aflash Decanter"},
    ]
    events = [
        {"machine_uid": "flash-rig", "ts": "2026-08-20T09:00:00",
         "kind": "qc", "lab_id": "FLASH", "test_name": "Density",
         "value": "61.5"},
        {"machine_uid": "flash-rig", "ts": "2026-08-26T09:00:00",
         "kind": "qc", "lab_id": "XX-FLASHBACK-9", "test_name": "Density",
         "value": "60.0"},
    ]
    return machines, events


class TestTheCapKeepsTheBestOfEachKind:

    def _answer(self, limit):
        machines, events = _flash_lab()
        return search_rows("flash", machines=machines, events=events,
                           limit=limit)

    def test_the_four_hits_rank_in_tier_order_when_nothing_is_capped(self):
        assert _labels(self._answer(25)) == [
            "FLASH", "Flash Rig", "XX-FLASHBACK-9", "Aflash Decanter"]

    def test_a_cap_of_two_keeps_the_two_best_not_the_two_worst(self):
        # The reported blocker: limit=2 answered with the 6520 and the 2417
        # and dropped the exact Lab ID and the prefix hit above them.
        assert _labels(self._answer(2)) == ["FLASH", "Flash Rig"]

    def test_a_cap_of_one_keeps_the_exact_lab_id(self):
        assert _labels(self._answer(1)) == ["FLASH"]

    def test_each_cap_is_a_prefix_of_the_uncapped_ranking(self):
        full = _labels(self._answer(25))
        for limit in (1, 2, 3, 4, 5):
            assert _labels(self._answer(limit)) == full[:limit], limit

    def test_the_reserved_slot_is_the_best_scoring_hit_of_its_kind(self):
        # Stated on the scores rather than the labels, so a reordering that
        # happens to look right for this fixture still fails.
        for limit in (1, 2, 3, 4):
            answer = self._answer(limit)
            best = {}
            machines, events = _flash_lab()
            everything = search_rows("flash", machines=machines,
                                     events=events, limit=25)["results"]
            for hit in everything:
                best.setdefault(hit["kind"], hit["score"])
            for kind in {h["kind"] for h in answer["results"]}:
                shown = [h["score"] for h in answer["results"]
                         if h["kind"] == kind]
                assert max(shown) == best[kind], (limit, kind)

    def test_the_one_matching_instrument_that_survives_is_the_best_one(self):
        # The documented guarantee — "the best hit of every kind that matched
        # is kept" — against thirty samples that would otherwise fill the page.
        machines, events = _flash_lab()
        answer = search_rows("flash", machines=machines,
                             events=events + _busy_lab_events(), limit=3)
        assert _labels(answer)[0] == "FLASH"
        assert "Flash Rig" in _labels(answer)
        assert "Aflash Decanter" not in _labels(answer)

    def test_the_pre_pick_order_is_not_what_decides_membership(self):
        # The reviewer deleted `_assemble`'s pre-pick sort and nothing failed.
        # Reversing the payload changes that order and must change nothing.
        machines, events = _flash_lab()
        forwards = search_rows("flash", machines=machines, events=events,
                               limit=2)
        backwards = search_rows("flash", machines=list(reversed(machines)),
                                events=list(reversed(events)), limit=2)
        assert _labels(forwards) == _labels(backwards) == ["FLASH", "Flash Rig"]


# ══════════════════════════════════════════════════════════════════════════
# Determinism. CLAUDE.md's stability section is the burn: two machines, a
# stable sort, and the payload's order deciding what is visible. This module
# must not borrow `build_machines`' ordering — it must have its own.
# ══════════════════════════════════════════════════════════════════════════

def _fleet(n=12, method="Flash Point"):
    return [{"machine_uid": "rig-%02d" % i, "title": "Rig %02d" % i,
             "level_uid": "lvl-ground",
             "effective_specs": [{"test_name": method,
                                  "sample_id": "Diesel - AO25"}]}
            for i in range(n)]


class TestThePayloadsOrderDecidesNothing:

    def test_a_methods_machine_list_survives_a_reversed_payload(self):
        fleet = _fleet(4)
        forwards = _only(search_rows("Flash Point", machines=fleet))
        backwards = _only(search_rows("Flash Point",
                                      machines=list(reversed(fleet))))
        assert forwards["machines"] == backwards["machines"]
        assert [m["machine_uid"] for m in forwards["machines"]] == [
            "rig-00", "rig-01", "rig-02", "rig-03"]

    def test_a_hits_navigation_target_survives_a_reversed_payload(self):
        # `machine_uid` on the hit is where the UI sends the reader. Flipping
        # the payload must not send them to a different bench.
        fleet = _fleet(4)
        forwards = _only(search_rows("Flash Point", machines=fleet))
        backwards = _only(search_rows("Flash Point",
                                      machines=list(reversed(fleet))))
        assert forwards["machine_uid"] == backwards["machine_uid"] == "rig-00"
        assert forwards["machine_title"] == backwards["machine_title"]

    def test_which_eight_of_twelve_are_listed_is_not_the_payloads_choice(self):
        # MAX_HIT_MACHINES compounds it: the cap takes the first eight of
        # whatever order this list is in.
        fleet = _fleet(12)
        forwards = _only(search_rows("Flash Point", machines=fleet))
        backwards = _only(search_rows("Flash Point",
                                      machines=list(reversed(fleet))))
        assert [m["machine_uid"] for m in forwards["machines"]] == [
            "rig-%02d" % i for i in range(lab_search.MAX_HIT_MACHINES)]
        assert forwards["machines"] == backwards["machines"]
        assert forwards["machine_count"] == backwards["machine_count"] == 12

    def test_a_standards_machine_list_survives_a_reversed_payload(self):
        fleet = _fleet(4)
        forwards = _only(search_rows("AO25", machines=fleet))
        backwards = _only(search_rows("AO25", machines=list(reversed(fleet))))
        assert forwards["machines"] == backwards["machines"]

    def test_a_levels_standing_list_survives_a_reversed_payload(self):
        fleet = _fleet(12)
        levels = [{"uid": "lvl-ground", "name": "Ground", "rank": 0}]
        forwards = _only(search_rows("Ground", machines=fleet, levels=levels))
        backwards = _only(search_rows("Ground", machines=list(reversed(fleet)),
                                      levels=levels))
        assert forwards["machines"] == backwards["machines"]
        assert [m["machine_uid"] for m in forwards["machines"]] == [
            "rig-%02d" % i for i in range(lab_search.MAX_HIT_MACHINES)]

    def test_two_instruments_with_the_SAME_title_still_order_totally(self):
        # The exact shape CLAUDE.md records: a tie in the sort key, a stable
        # sort, and payload order left holding the decision.
        fleet = [{"machine_uid": "rig-b", "title": "Twin Rig",
                  "effective_specs": [{"test_name": "Flash Point"}]},
                 {"machine_uid": "rig-a", "title": "Twin Rig",
                  "effective_specs": [{"test_name": "Flash Point"}]}]
        forwards = _only(search_rows("Flash Point", machines=fleet))
        backwards = _only(search_rows("Flash Point",
                                      machines=list(reversed(fleet))))
        assert [m["machine_uid"] for m in forwards["machines"]] == [
            "rig-a", "rig-b"]
        assert forwards["machines"] == backwards["machines"]

    def test_the_title_orders_the_list_even_when_the_uid_disagrees(self):
        # Every other fixture here numbers uids and titles the same way, so a
        # key that had quietly become "uid only" would look right in all of
        # them. `build_machines` orders the floor by title and so does this.
        fleet = [{"machine_uid": "z-rig", "title": "Alpha Rig",
                  "effective_specs": [{"test_name": "Flash Point"}]},
                 {"machine_uid": "a-rig", "title": "Beta Rig",
                  "effective_specs": [{"test_name": "Flash Point"}]}]
        for order in (fleet, list(reversed(fleet))):
            hit = _only(search_rows("Flash Point", machines=order))
            assert [m["machine_uid"] for m in hit["machines"]] == [
                "z-rig", "a-rig"]
            assert hit["machine_title"] == "Alpha Rig"

    def test_a_sample_still_lists_its_benches_newest_first(self):
        # Recency is the rule for samples and the new total ordering must not
        # have quietly replaced it with a name sort.
        hit = _only(_find("L-37712"))
        assert [m["machine_uid"] for m in hit["machines"]] == [
            "pac-flash-2", "pac-flash-1"]


class TestATimestampTieIsBrokenByTheRowNotByThePayload:
    """A meta panel that flips its reading between refreshes is worse than one
    that shows nothing: an assessor reads it as the record disagreeing with
    itself."""

    def _tied(self):
        return [
            {"machine_uid": "pac-flash-2", "ts": "2026-08-26T09:12:00",
             "kind": "qc", "lab_id": "L-37712", "test_name": "Flash Point",
             "value": "61.5"},
            {"machine_uid": "multitek-s", "ts": "2026-08-26T09:12:00",
             "kind": "qc", "lab_id": "l 37712", "test_name": "Sulfur",
             "value": "0.42"},
        ]

    def test_every_ordering_of_a_tied_pair_reports_the_same_reading(self):
        import itertools
        seen = set()
        for order in itertools.permutations(self._tied()):
            hit = _only(_find("37712", events=list(order)))
            seen.add((hit["label"], hit["meta"]["value"],
                      hit["meta"]["test_name"]))
        assert len(seen) == 1, seen

    def test_a_three_way_tie_is_still_one_answer(self):
        rows = self._tied() + [
            {"machine_uid": "optimpp-1", "ts": "2026-08-26T09:12:00",
             "kind": "qc", "lab_id": "L-37712", "test_name": "Density",
             "value": "0.83"}]
        import itertools
        seen = {tuple(sorted(_only(_find("37712", events=list(order)))["meta"]
                             .items()))
                for order in itertools.permutations(rows)}
        assert len(seen) == 1, seen

    def test_a_later_row_still_wins_outright(self):
        # The tie-break may only decide TIES; recency decides everything else.
        # The newest row here is deliberately the one whose OWN content sorts
        # LOWEST — `L 37712` below `l37712`, `Flash Point` below `Sulfur` — so
        # a stamp that had lost its timestamp would answer with the older row.
        rows = [
            {"machine_uid": "multitek-s", "ts": "2026-08-25T14:03:00",
             "kind": "qc", "lab_id": "l37712", "test_name": "Sulfur",
             "value": "0.42"},
            {"machine_uid": "pac-flash-2", "ts": "2026-08-26T09:12:00",
             "kind": "qc", "lab_id": "L 37712", "test_name": "Flash Point",
             "value": "61.5"},
        ]
        for order in (rows, list(reversed(rows))):
            hit = _only(_find("37712", events=list(order)))
            assert hit["label"] == "L 37712"
            assert hit["meta"]["value"] == "61.5"
            assert hit["meta"]["test_name"] == "Flash Point"

    def test_one_spelling_of_a_method_is_chosen_by_the_name_not_the_order(self):
        # `Flash Point` and `flash point` fold to one key and one of the two
        # raw spellings is shown. Which one may not be the payload's choice.
        fleet = [{"machine_uid": "a", "title": "A",
                  "effective_specs": [{"test_name": "Flash Point"}]},
                 {"machine_uid": "b", "title": "B",
                  "effective_specs": [{"test_name": "flash point"}]}]
        forwards = _only(search_rows("flashpoint", machines=fleet))
        backwards = _only(search_rows("flashpoint",
                                      machines=list(reversed(fleet))))
        assert forwards["label"] == backwards["label"] == "Flash Point"


# ══════════════════════════════════════════════════════════════════════════
# Every cap is in the answer. A cap that changes what comes back and reports
# nothing is the "Showing 25 of 313" problem with the count left off.
# ══════════════════════════════════════════════════════════════════════════

class TestTheTokenCapIsReportedLikeEveryOtherCap:

    def test_the_token_cap_is_in_every_answer(self):
        for typed in ("", "f", "zzzquux", "flash"):
            answer = _find(typed)
            assert answer["max_query_tokens"] == lab_search.MAX_QUERY_TOKENS
            assert answer["query_tokens_capped"] is False, typed

    def test_a_query_past_the_token_cap_says_it_was_capped(self):
        typed = " ".join("w%d" % n for n in
                         range(lab_search.MAX_QUERY_TOKENS + 1))
        assert _find(typed)["query_tokens_capped"] is True

    def test_one_more_correct_word_never_bounds_the_answer_in_silence(self):
        # Eight words of a name match. A NINTH, equally correct, turns the hit
        # into "no results" — because past the cap the tokens tier is refused
        # rather than approved on a sample of the query. That is the right
        # refusal and the wrong silence: the flag has to say it happened.
        fleet = [{"machine_uid": "rig", "title":
                  "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India"}]
        eight = search_rows("hotel golf foxtrot echo delta charlie bravo alpha",
                            machines=fleet)
        nine = search_rows(
            "india hotel golf foxtrot echo delta charlie bravo alpha",
            machines=fleet)
        assert eight["state"] == STATE_OK
        assert eight["results"][0]["match"] == "tokens"
        assert eight["query_tokens_capped"] is False
        # Refused, and it says so. Without the refusal this answers OK on a
        # check of only the first eight words.
        assert nine["state"] == STATE_NO_MATCH, _labels(nine)
        assert nine["query_tokens_capped"] is True

    def test_the_cap_is_the_refusal_it_claims_to_be(self):
        # Nine words, of which the ninth is WRONG. Approving the query on a
        # sample of itself would answer with the instrument anyway.
        fleet = [{"machine_uid": "rig", "title":
                  "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India"}]
        answer = search_rows(
            "alpha bravo charlie delta echo foxtrot golf hotel zulu",
            machines=fleet)
        assert answer["state"] == STATE_NO_MATCH, _labels(answer)
        assert answer["query_tokens_capped"] is True

    def test_the_index_says_what_it_was_built_from(self):
        # `SearchIndex.counts` exists so a caller can prove the index it is
        # serving from was built from the rows it thinks it was. It could not:
        # nothing ever put it in an answer.
        answer = _find("flash")
        assert answer["indexed"]["equipment"] == 4
        assert answer["indexed"]["sample"] == 2
        assert answer["indexed"]["level"] == 2
        assert sum(answer["indexed"].values()) == answer["searched"]

    def test_machines_truncated_is_false_when_nothing_was_dropped(self):
        # Only ever asserted in its True case, so hard-wiring it to True left
        # the suite green. A flag set when nothing was dropped is worse than
        # no flag at all.
        hit = _only(search_rows("Flash Point", machines=_fleet(3)))
        assert hit["machine_count"] == 3
        assert len(hit["machines"]) == 3
        assert hit["machines_truncated"] is False
        exactly = _only(search_rows("Flash Point",
                                    machines=_fleet(lab_search.MAX_HIT_MACHINES)))
        assert exactly["machines_truncated"] is False

    def test_the_limit_ceiling_is_a_real_number_not_a_self_reference(self):
        # `test_the_limit_is_clamped` compares against MAX_LIMIT itself, so
        # MAX_LIMIT = 100000 left the suite green.
        assert lab_search.MAX_LIMIT == 200
        assert lab_search.DEFAULT_LIMIT == 25
        assert lab_search.PER_KIND_LIMIT == 10
        assert lab_search.MAX_HIT_MACHINES == 8
        assert lab_search.MIN_QUERY_CHARS == 2
        assert lab_search.MAX_QUERY_CHARS == 128
        assert lab_search.MAX_QUERY_TOKENS == 8

    def test_no_answer_can_be_longer_than_the_per_kind_caps_allow(self):
        # The real ceiling on a result list, and it is not MAX_LIMIT.
        machines, events, levels = _big_lab()
        index = lab_search.build_index(machines=machines, events=events,
                                       levels=levels)
        answer = lab_search.search("00", index, limit=10 ** 9)
        assert answer["limit"] == lab_search.MAX_LIMIT
        assert len(answer["results"]) <= (lab_search.PER_KIND_LIMIT
                                          * len(lab_search.KINDS))


class TestWithinATierTheKindDecides:
    """Documented as arithmetic (`min gap between kinds > COVERAGE_MAX`) and
    tested only as arithmetic — removing KIND_WEIGHT from the score left all
    70 tests green."""

    def test_a_favoured_kind_wins_a_tier_even_with_worse_coverage(self):
        # `Sulfur Test` covers more of its field than `Sulfur Analyser` does of
        # its. Equipment still wins, because kind outranks coverage by design.
        answer = search_rows("sulfur", machines=[
            {"machine_uid": "sulfur-analyser", "title": "Sulfur Analyser",
             "effective_specs": [{"test_name": "Sulfur Test"}]}])
        assert _kinds(answer) == ["equipment", "method"]
        assert _labels(answer) == ["Sulfur Analyser", "Sulfur Test"]

    def test_the_gap_between_two_kinds_is_exactly_their_weights(self):
        answer = search_rows(
            "STD-1",
            machines=[{"machine_uid": "std-1", "title": "STD-1"}],
            events=[{"machine_uid": "std-1", "ts": "2026-08-26T08:40:00",
                     "kind": "qc", "lab_id": "STD-1", "test_name": "Sulfur",
                     "value": "0.42"}])
        by_kind = {hit["kind"]: hit["score"] for hit in answer["results"]}
        assert by_kind["sample"] - by_kind["equipment"] == (
            lab_search.KIND_WEIGHT["sample"]
            - lab_search.KIND_WEIGHT["equipment"])


# ══════════════════════════════════════════════════════════════════════════
# What a LIMS or an Excel export pastes into the box.
# ══════════════════════════════════════════════════════════════════════════

class TestTextPastedOutOfAnotherSystem:

    def test_full_width_characters_fold_to_the_ascii_the_lab_uses(self):
        assert squash("ＰＡＣ　Ｆｌａｓｈ"
                      "　２") == "pacflash2"

    def test_a_full_width_paste_finds_the_instrument(self):
        hit = _only(_find("ＰＡＣ　Ｆｌａｓ"
                          "ｈ　２"))
        assert hit["machine_uid"] == "pac-flash-2"
        assert hit["match"] == "exact"

    def test_a_ligature_is_the_letters_it_is_made_of(self):
        assert squash("ﬂash") == "flash"
        assert "Flash Point" in _labels(_find("ﬂash"))

    def test_a_superscript_digit_is_a_digit(self):
        assert squash("²") == "2"
        assert _only(_find("PAC Flash ²"))["machine_uid"] == "pac-flash-2"

    def test_an_accent_is_not_a_different_letter(self):
        assert squash("café") == "cafe"
        assert squash("Müller") == "muller"
        assert squash("İso") == "iso"

    def test_an_accent_does_not_split_a_word_in_two(self):
        # The decomposition leaves a combining mark behind, and treating it as
        # a separator makes `Müller` two words — so `ller` would come back at
        # the WORD tier, ranked as though somebody had typed a word of the
        # name. `squash` alone cannot see this: the squashed text is `muller`
        # either way, and only the boundaries move.
        fleet = [{"machine_uid": "muller-rig", "title": "Müller Rig"}]
        assert _only(search_rows("Muller", machines=fleet))["match"] == "prefix"
        assert _only(search_rows("ller", machines=fleet))["match"] == "substring"

    def test_folding_is_still_idempotent(self):
        for text in ("PAC Flash 2", "ﬂash", "²", "café"):
            assert squash(squash(text)) == squash(text), text


class TestAHitCanBeKeyedWithoutCollidingAcrossKinds:

    def test_the_same_id_on_two_kinds_is_two_different_keys(self):
        # `STD-1` is a Lab ID that was run AND a QC standard, so `id` alone
        # collides and a UI keying on it draws one row where there are two.
        answer = _find("STD-1")
        ids = [hit["id"] for hit in answer["results"]]
        keys = [hit["key"] for hit in answer["results"]]
        assert len(ids) > len(set(ids))          # the collision is real
        assert len(keys) == len(set(keys))       # and the key survives it

    def test_two_hits_of_the_SAME_kind_are_two_different_keys(self):
        # The other half: a key of just the kind is unique across kinds and
        # collides on every page that returns two instruments.
        answer = _find("flash")
        keys = [hit["key"] for hit in answer["results"]]
        kinds = [hit["kind"] for hit in answer["results"]]
        assert len(kinds) > len(set(kinds))       # two equipment hits
        assert len(keys) == len(set(keys))

    def test_a_key_is_stable_across_two_searches_of_one_index(self):
        index = lab_search.build_index(machines=_machines(),
                                       events=_events(), levels=_levels())
        first = lab_search.search("STD-1", index)
        second = lab_search.search("STD-1", index)
        assert [h["key"] for h in first["results"]] == [
            h["key"] for h in second["results"]]


# ══════════════════════════════════════════════════════════════════════════
# The tripwires, made falsifiable. Five of the nine banned strings could not
# appear in what was being searched: `_code_only` joined tokens with spaces,
# so `re.compile(x)` reached the assertion as `re . compile ( x )`.
# ══════════════════════════════════════════════════════════════════════════

_EVIL = '''"""A module that documents itself, and then does the thing."""
_re = __import__("re")
_PAT = _re.compile(r"(a+)+$")


def _evil(text):
    """This is prose about re.compile and it must stay allowed."""
    return bool(_PAT.search(text)) and bool(eval("1+1"))
'''


class TestTheStripperKeepsTheCodeItIsSearching:

    def test_a_dotted_call_survives_stripping_verbatim(self):
        # The defect: tokenising and re-joining with spaces meant the five
        # dotted/called strings in the banned list could never be found.
        code = _strip_prose(_EVIL)
        for banned in ("re.compile", "_PAT.search", "eval(", "__import__("):
            assert banned in code, banned

    def test_prose_is_still_stripped_so_the_file_can_explain_itself(self):
        code = _strip_prose('"""So is re.search, says the docstring."""\n'
                            'x = 1  # re.compile is banned\n')
        assert "re.compile" not in code
        assert "re.search" not in code
        assert "x = 1" in code

    def test_the_modules_own_prose_is_not_what_the_tripwire_reads(self):
        # The reason the helper exists at all: the docstring explaining why
        # there is no `re` here says "re", and the comment explaining why it
        # cannot reach LabCore says "LabCore".
        assert "labcore" in _source().lower()
        assert "labcore" not in _code_only().lower()


def _regex_offences(source: str):
    """Every syntactic route out of `str` methods and into a pattern engine.

    A substring check over the source cannot see `__import__("re")`, and that
    is not a hypothetical: the reviewer appended exactly that, plus a compiled
    catastrophic pattern and an `eval`, and all four tripwires stayed green.
    So the guard reads the parse tree instead — which also means the module's
    prose is exempt for free, because no docstring is a Name or an Attribute.
    """
    import ast

    banned_names = {"__import__", "eval", "exec", "compile", "getattr",
                    "globals", "vars", "importlib", "re", "regex", "fnmatch",
                    "sre_compile", "sre_parse", "os", "subprocess"}
    banned_attrs = {"compile", "search", "match", "fullmatch", "findall",
                    "finditer", "sub", "subn", "escape", "system", "popen",
                    "import_module"}
    offences = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in banned_names:
            offences.append("name:" + node.id)
        elif isinstance(node, ast.Attribute) and node.attr in banned_attrs:
            offences.append("attr:" + node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in banned_names:
                    offences.append("import:" + alias.name)
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in banned_names:
                offences.append("from:" + node.module)
    return offences


class TestTheGuardIsSoundNotJustGreen:

    def test_the_guard_catches_the_module_the_reviewer_appended(self):
        # If this passes trivially the guard proves nothing, so the evil
        # module is checked FIRST and by name.
        offences = _regex_offences(_EVIL)
        assert "name:__import__" in offences
        assert "attr:compile" in offences
        assert "attr:search" in offences
        assert "name:eval" in offences

    def test_a_module_that_only_TALKS_about_re_is_not_an_offence(self):
        assert _regex_offences('"""No re.compile here, and none wanted."""\n'
                               'x = "re.search"  # nor here\n') == []

    def test_lab_search_has_no_route_to_a_pattern_engine_at_all(self):
        assert _regex_offences(_source()) == []

    def test_the_banned_substrings_are_findable_in_stripped_code(self):
        # Five of the nine were unfalsifiable. Assert the stripper would find
        # each of them if it were there, so the list below means something.
        for banned in ("import re", "re.compile", "re.search", "re.match",
                       "fnmatch", "eval(", "exec(", "LIKE ", "GLOB "):
            planted = _strip_prose("x = 1\ny = '%s'\n" % banned)
            assert banned in planted, banned


# ══════════════════════════════════════════════════════════════════════════
# The wiring gate. `levels.py` shipped fully tested and connected to nothing;
# declared-but-inert and working look identical from the outside.
# ══════════════════════════════════════════════════════════════════════════

class TestTheSearchBoxIsWiredToTheApp:

    def _app(self, gateway):
        import snapshot_service
        from web_app import create_app
        snapshot_service.SnapshotService(gateway).ensure_schema()
        gateway.sql(
            "INSERT INTO lem_machine_status (machine_uid, title, status, "
            "reason, updated_at) VALUES (?, ?, 'GREEN', '', "
            "'2026-08-01T09:00:00')", ["pac-flash-2", "PAC Flash 2"])
        app = create_app(gateway, secret="t")
        app.config.update(TESTING=True)
        return app

    def test_the_route_exists_and_answers_with_the_modules_shape(self):
        from labcore_gateway import FakeLabCoreGateway
        client = self._app(FakeLabCoreGateway()).test_client()
        client.get("/api/machines?fresh=1")
        body = client.get("/api/search?q=flash").get_json()
        assert body["state"] in (STATE_IDLE, STATE_OK, STATE_NO_MATCH)
        for key in ("results", "matched", "shown", "limit", "counts",
                    "kinds", "truncated", "query_truncated",
                    "max_query_tokens", "query_tokens_capped"):
            assert key in body, key

    def test_typing_finds_the_instrument_through_the_route(self):
        from labcore_gateway import FakeLabCoreGateway
        client = self._app(FakeLabCoreGateway()).test_client()
        client.get("/api/machines?fresh=1")
        body = client.get("/api/search?q=PAC+Flash+2").get_json()
        assert body["state"] == STATE_OK
        assert body["results"][0]["machine_uid"] == "pac-flash-2"

    def test_a_keystroke_costs_zero_labcore_operations(self):
        # The reason `build_index` is split from `search`. One read per
        # KEYSTROKE per viewer is strictly worse than the 17-ops-per-refresh
        # that `snapshot_service` was built to end.
        from labcore_gateway import FakeLabCoreGateway

        class Counting(FakeLabCoreGateway):
            def __init__(self):
                super().__init__()
                self.reads = []
                self.writes = []

            def read_sql(self, sql, args=None, **kw):
                self.reads.append(sql)
                return super().read_sql(sql, args, **kw)

            def sql(self, sql, args=None, **kw):
                self.writes.append(sql)
                return super().sql(sql, args, **kw)

        gateway = Counting()
        client = self._app(gateway).test_client()
        client.get("/api/machines?fresh=1")
        client.get("/api/search?q=f")
        gateway.reads.clear()
        gateway.writes.clear()
        for typed in ("f", "fl", "fla", "flas", "flash", "flash ", "flash 2"):
            assert client.get("/api/search?q=" + typed).status_code == 200
        assert gateway.reads == [], gateway.reads
        assert gateway.writes == [], gateway.writes
