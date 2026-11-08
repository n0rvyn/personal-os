"""English calibration regression: preserve meaningful duplicate checks."""
from lib import dedup


def test_shared_english_terms_are_not_duplicate_claims():
    text = "## ① First\n\nA vulnerability is a weakness in software that may allow unwanted access.\n\n## ② Second\n\nFinding a vulnerability can help developers decide where further investigation is needed."
    assert dedup.check_intra_dup(text)["ok"] is False
    assert dedup.check_intra_dup_english(text)["ok"] is True


def test_exact_repeated_sentence_blocks():
    sentence = "Researchers need clear permission before they examine computer systems owned by another organization."
    text = "One question concerns access. " + sentence + "\n\nA second topic concerns review. " + sentence
    assert not dedup.check_intra_dup_english(text)["ok"]


def test_embedded_repeated_claim_blocks():
    claim = "the reported estimate describes the pace of capability progress rather than the execution time of individual tasks"
    text = "A cautious reading says " + claim + ".\n\nAnother section repeats that " + claim + " before discussing other issues."
    assert not dedup.check_intra_dup_english(text)["ok"]


def test_repeated_short_sentence_blocks():
    assert not dedup.check_intra_dup_english("Testing still needs clear limits.\n\nTesting still needs clear limits.")["ok"]


def test_different_claims_with_same_subject_pass():
    assert dedup.check_intra_dup_english("The company announced a proposed acquisition subject to closing conditions.\n\nThe company reports benchmark results from a controlled evaluation.")["ok"]


def test_duplicate_sentences_inside_same_paragraph_block():
    assert not dedup.check_intra_dup_english("Testing still needs clear limits. Testing still needs clear limits.")["ok"]


def test_heading_without_blank_line_keeps_content():
    text = "## ① First\nTesting still needs clear limits.\n\n## ② Second\nTesting still needs clear limits."
    assert not dedup.check_intra_dup_english(text)["ok"]


def test_english_scorecard_routes_language_specific_audit():
    from lib.scorecard import build_scorecard
    body = "## ① First\n\nA vulnerability is a weakness in software that may allow unwanted access.\n\n## ② Second\n\nFinding a vulnerability can help developers decide where further investigation is needed."
    arguments = dict(body=body, script_text=body, show="morning", score_verdict=None, factcheck_verdict=None, store={"anchors": {}}, today="2026-11-07", judge_verdict=None)
    english = build_scorecard(**arguments, output_language="en")
    chinese = build_scorecard(**arguments)
    assert next(x for x in english["hard_gates"] if x["name"] == "intra_dup")["ok"]
    assert not next(x for x in chinese["hard_gates"] if x["name"] == "intra_dup")["ok"]
