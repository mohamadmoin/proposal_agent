from engine.references import authors_text, number_document, vancouver
from engine.schemas import PaperRecord

A = PaperRecord(pmid="1", title="Alpha study.", journal_abbrev="J A", year=2020, volume="5", issue="2", pages="1-9",
                doi="10.1/a", authors=["Smith J", "Ali R"])  # fmt: skip
B = PaperRecord(pmid="2", title="Beta study", journal="Journal B", year=2021, authors=["Doe J"])
CHAPTER = PaperRecord(pmid="30335299", title="Vitamin D Deficiency", year=2025, is_book=True,
                      book_accession="NBK532266", authors=["Kaur J", "Khare S", "Givler A"])  # fmt: skip


def test_numbers_follow_first_appearance_across_sections():
    texts = ["Intro cites {S1} then {P2}.", "Background {P1}. Again {S1}.", "Methods {M1}."]
    sources = {"S1": CHAPTER, "P1": A, "P2": B, "M1": A}  # M1 is the same paper as P1
    result = number_document(texts, sources)
    assert result.texts == ["Intro cites [1] then [2].", "Background [3]. Again [1].", "Methods [3]."]
    assert [r["number"] for r in result.references] == [1, 2, 3]
    assert result.references[2]["tokens"] == ["P1", "M1"]


def test_adjacent_tokens_share_one_bracket():
    result = number_document(["Both agree {P1}{P2}.", "Also {P2}, {P1}."], {"P1": A, "P2": B})
    assert result.texts == ["Both agree [1, 2].", "Also [1, 2]."]


def test_unknown_tokens_are_removed_and_logged():
    result = number_document(["Known {P1} and unknown {P9}.", "Only unknown {X1}."], {"P1": A})
    assert result.texts == ["Known [1] and unknown.", "Only unknown."]
    assert result.unknown_tokens == ["P9", "X1"]
    assert len(result.references) == 1


def test_every_reference_is_cited_and_every_citation_resolves():
    result = number_document(["{P1} {S1}"], {"P1": A, "P2": B, "S1": CHAPTER})
    assert [r["pmid"] for r in result.references] == ["1", "30335299"]  # P2 was never cited: not listed


def test_vancouver_article():
    assert vancouver(A) == "Smith J, Ali R. Alpha study. J A. 2020;5(2):1-9. doi:10.1/a. PMID: 1."
    assert vancouver(B) == "Doe J. Beta study. Journal B. 2021. PMID: 2."


def test_vancouver_et_al_after_six():
    names = [f"Author{i} A" for i in range(1, 9)]
    assert authors_text(names) == "Author1 A, Author2 A, Author3 A, Author4 A, Author5 A, Author6 A, et al."


def test_vancouver_statpearls_chapter():
    assert vancouver(CHAPTER) == (
        "Kaur J, Khare S, Givler A. Vitamin D Deficiency. In: StatPearls [Internet]. Treasure Island (FL): "
        "StatPearls Publishing; 2025. Available from: https://www.ncbi.nlm.nih.gov/books/NBK532266/"
    )
