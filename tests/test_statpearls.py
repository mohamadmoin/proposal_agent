from engine.statpearls import cited_papers, find_chapter, gather_sources, parse_uploaded_html
from engine.store import MemoryStore
from tests.conftest import FIXTURES

PAGE = (FIXTURES / "statpearls_page.html").read_text(encoding="utf-8")
PROFILE_TEXT = "Prevalence of vitamin D deficiency among pregnant women vitamin D deficiency pregnancy"


def test_uploaded_page_sections_and_references():
    page = parse_uploaded_html(PAGE)
    assert page.title == "Vitamin D Deficiency"
    assert page.nbk == "NBK532266"
    headings = [h for h, _ in page.sections]
    assert headings == ["Vitamin D Deficiency", "Introduction", "Epidemiology"]
    intro = dict(page.sections)["Introduction"]
    assert intro == "Vitamin D deficiency is a common condition. It affects bone health in 2 main ways."
    assert "Risk is higher in pregnancy." in dict(page.sections)["Epidemiology"]
    assert page.reference_pmids == ["31101452", "12897318", "24239505"]


def test_scripts_styles_and_navigation_are_dropped():
    page = parse_uploaded_html(PAGE)
    text = page.text()
    assert "tracking" not in text and "font-family" not in text
    assert "Home" not in text and "Footer" not in text
    assert "99999999" not in page.reference_pmids


def test_api_chapter_is_the_most_similar_title(offline_pubmed):
    store = MemoryStore()
    chapter = find_chapter(offline_pubmed, "vitamin D deficiency", PROFILE_TEXT, store)
    assert chapter.title == "Vitamin D Deficiency" and chapter.book_accession == "NBK532266"
    assert store.searches[0]["query"] == "(vitamin D deficiency) AND statpearls[book]"


def test_cited_papers_are_limited_to_8_with_abstracts(offline_pubmed):
    chapter = find_chapter(offline_pubmed, "vitamin D deficiency", PROFILE_TEXT, MemoryStore())
    papers = cited_papers(offline_pubmed, chapter.reference_pmids, PROFILE_TEXT, MemoryStore())
    assert len(papers) == 8
    assert all(p.pmid in chapter.reference_pmids and len(p.abstract) >= 200 for p in papers)


def test_sources_from_the_api(offline_pubmed):
    sources, origin = gather_sources(offline_pubmed, "vitamin D deficiency", PROFILE_TEXT, "", MemoryStore())
    assert origin == "api"
    assert [s.token for s in sources] == [f"S{i}" for i in range(1, 10)]
    assert sources[0].kind == "chapter" and "Sections of the chapter" in sources[0].text


def test_sources_from_an_upload_use_the_page_text(offline_pubmed):
    sources, origin = gather_sources(offline_pubmed, "vitamin D deficiency", PROFILE_TEXT, PAGE, MemoryStore())
    assert origin == "upload"
    assert sources[0].record.book_accession == "NBK532266"  # matched to the API record
    assert "It affects bone health" in sources[0].text
    assert {s.record.pmid for s in sources[1:]} <= {"31101452", "12897318", "24239505"}
