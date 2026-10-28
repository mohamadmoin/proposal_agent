"""AGT-3.1 / AGT-3.4: PubMed efetch survives an HTTP 400 (only the bad id is skipped) and sample sizes take a design
effect before attrition. Pure unit tests, no network (httpx MockTransport)."""
import unittest

import httpx

from engine import samplesize
from engine.pubmed import PubMedClient

XML = ('<PubmedArticleSet>{}</PubmedArticleSet>')
ART = ('<PubmedArticle><MedlineCitation><PMID>{p}</PMID><Article><ArticleTitle>T{p}</ArticleTitle>'
       '<Abstract><AbstractText>Abstract of {p} long enough.</AbstractText></Abstract></Article>'
       '</MedlineCitation></PubmedArticle>')


def client(bad: set[str]) -> PubMedClient:
    def handler(request: httpx.Request) -> httpx.Response:
        ids = dict(httpx.QueryParams(request.content.decode()))["id"].split(",")
        if bad & set(ids):
            return httpx.Response(400, text="bad id")
        return httpx.Response(200, text=XML.format("".join(ART.format(p=p) for p in ids)))
    c = PubMedClient(http=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)
    c.limiter.wait = lambda: None
    return c


class PubMedHardeningTests(unittest.TestCase):
    def test_bad_id_is_skipped_not_fatal(self):
        c = client({"13"})
        records = c.efetch([str(i) for i in range(10, 18)])
        self.assertEqual(sorted(r.pmid for r in records), [str(i) for i in range(10, 18) if i != 13])
        self.assertEqual(c.skipped, ["13"])

    def test_clean_fetch_unchanged(self):
        c = client(set())
        self.assertEqual(len(c.efetch(["1", "2", "2", ""])), 2)
        self.assertEqual(c.skipped, [])


class DesignEffectTests(unittest.TestCase):
    def test_deff_before_attrition(self):
        base = samplesize.compute("single_proportion", {"p": 0.5, "d": 0.05})
        self.assertEqual(base.n1, 385)  # 384.15 → 385, unchanged without DEFF
        r = samplesize.compute("single_proportion", {"p": 0.5, "d": 0.05}, rate=0.1, design_effect=1.5)
        self.assertEqual(r.n1, 577)  # 384.15 × 1.5 = 576.22 → 577
        self.assertEqual(r.final_total, 642)  # 577 / 0.9 = 641.1 → 642
        self.assertIn("1.5", samplesize.worked_calculation(r, {}, "en"))

    def test_two_groups_and_bounds(self):
        r = samplesize.compute("two_proportions", {"p1": 0.3, "p2": 0.15}, design_effect=2)
        plain = samplesize.compute("two_proportions", {"p1": 0.3, "p2": 0.15})
        self.assertEqual(r.n1, samplesize.ceil_n(plain.raw_n1 * 2))
        with self.assertRaises(samplesize.SampleSizeError):
            samplesize.compute("single_proportion", {"p": 0.5, "d": 0.05}, design_effect=0.5)


class DiagnosticAccuracyTests(unittest.TestCase):
    """AGT-17: Buderer's formula (knowledge/sample_size.md reference values)."""

    def test_reference_values(self):
        r = samplesize.compute("diagnostic_accuracy",
                               {"sensitivity": 0.85, "specificity": 0.9, "prevalence": 0.45, "d": 0.06})
        self.assertAlmostEqual(r.extra["n_sensitivity"], 302.34, places=2)
        self.assertAlmostEqual(r.extra["n_specificity"], 174.61, places=2)
        self.assertEqual(r.final_total, 303)

    def test_sensitivity_only_and_percentages(self):
        r = samplesize.compute("diagnostic_accuracy", {"sensitivity": 85, "prevalence": 45, "d": 0.06}, rate=0.1)
        self.assertEqual(r.n1, 303)  # driven by the diseased participants: 136.05 / 0.45
        self.assertEqual(r.final_total, 337)
        self.assertIn("136.05", samplesize.worked_calculation(r, {}, "fa"))
        with self.assertRaises(samplesize.SampleSizeError):
            samplesize.compute("diagnostic_accuracy", {"sensitivity": 0.85, "d": 0.06})  # prevalence is required


if __name__ == "__main__":
    unittest.main()
