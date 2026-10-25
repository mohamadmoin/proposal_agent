from engine.verify import (
    canonical,
    contains_number,
    evidence_text,
    extract_numbers,
    missing_numbers,
    normalise_digits,
    numbered_sentences,
    split_sentences,
    valid_evidence,
)


def test_split_keeps_decimals_and_p_values():
    text = "Mean dose was 1.5 mg daily. The difference was significant (p < 0.05). Results held."
    assert split_sentences(text) == [
        "Mean dose was 1.5 mg daily.",
        "The difference was significant (p < 0.05).",
        "Results held.",
    ]


def test_split_keeps_abbreviations():
    text = "Smith et al. reported a rise. Group A vs. group B differed. Fig. 2 shows it. Next sentence."
    assert split_sentences(text) == [
        "Smith et al. reported a rise.",
        "Group A vs. group B differed.",
        "Fig. 2 shows it.",
        "Next sentence.",
    ]


def test_split_handles_labelled_lines_and_initials():
    text = "BACKGROUND: Vitamin D matters. It is common.\nRESULTS: Prevalence was 45.6%. J. Smith agreed."
    assert split_sentences(text) == [
        "BACKGROUND: Vitamin D matters.",
        "It is common.",
        "RESULTS: Prevalence was 45.6%.",
        "J. Smith agreed.",
    ]


def test_numbered_sentences():
    joined, sentences = numbered_sentences("One. Two.")
    assert joined == "{1} One. {2} Two."
    assert sentences == ["One.", "Two."]


def test_digit_normalisation():
    assert normalise_digits("۱۲۳٫۵ و ٤٥") == "123.5 و 45"
    assert normalise_digits("2·5") == "2.5"


def test_canonical_forms():
    assert canonical(".5") == "0.5"
    assert canonical("0.50") == "0.5"
    assert canonical("1,234") == "1234"
    assert canonical("12.0") == "12"


def test_extract_numbers_ignores_tokens():
    assert extract_numbers("In 2019, 45% of 1,200 women {P3} had .5 units") == [
        ("2019", False),
        ("45", True),
        ("1200", False),
        ("0.5", False),
    ]


def test_missing_numbers_with_percentages_both_ways():
    source = "Deficiency affected 45% of women; the OR was 0.62 (95% CI 0.4-0.9)."
    assert missing_numbers("Deficiency was 45%", source) == []
    assert missing_numbers("A proportion of 0.45 was deficient", source) == []
    assert missing_numbers("OR 62%", source) == []
    assert missing_numbers("Deficiency was 47%", source) == ["47"]
    assert missing_numbers("۴۵ درصد", source) == []
    assert missing_numbers("In 2020, 45%", source, allowed={"2020"}) == []


def test_contains_number_after_digit_normalisation():
    assert contains_number("حجم نمونه ۳۸۵ نفر است", 385)
    assert not contains_number("n = 3850", 385)


def test_evidence_checks():
    sentences = ["A.", "B.", "C."]
    assert valid_evidence([1, 3], 3)
    assert not valid_evidence([4], 3)
    assert not valid_evidence([], 3)
    assert evidence_text([3, 1], sentences) == "C. A."


def test_percent_written_as_a_word_counts_both_ways():
    from engine.verify import number_set

    assert "0.038" in number_set("the prevalence of IDA was 3.8 percent")
    assert "0.25" in number_set("25 per cent of students")
    assert "0.4" in number_set("شیوع ۴۰ درصد بود")
