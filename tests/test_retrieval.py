import unittest
from pathlib import Path

from sel.pack import Pack, load_pack
from sel.retrieval import PassageIndex, split_passages, tokenize

ROOT = Path(__file__).resolve().parent.parent
SOLAR = load_pack(ROOT / "packs/demo-solar")
POS = load_pack(ROOT / "packs/demo-pos")


def pack_with_docs(docs: dict[str, str]) -> Pack:
    return Pack(
        root=ROOT, id="test", name="Test", description="", languages=[],
        week_starts_on="sunday", scorecard=[], facts={}, docs=docs, templates={},
    )


class DemoPacks(unittest.TestCase):
    def test_every_passage_is_an_exact_substring_of_its_source(self):
        for pack in (SOLAR, POS):
            for passage in split_passages(pack):
                with self.subTest(pack=pack.id, source=passage.source, text=passage.text[:40]):
                    source_text = pack.source_text(passage.source)
                    self.assertIsNotNone(source_text)
                    self.assertIn(passage.text, source_text)
                    self.assertEqual(passage.text, passage.text.strip())

    def test_every_fact_is_one_passage(self):
        for pack in (SOLAR, POS):
            with self.subTest(pack=pack.id):
                fact_passages = [p for p in split_passages(pack) if p.source.startswith("fact:")]
                self.assertEqual(
                    sorted(p.source for p in fact_passages),
                    sorted(f"fact:{fact_id}" for fact_id in pack.facts),
                )
                for passage in fact_passages:
                    self.assertEqual(passage.text, pack.facts[passage.source[5:]].statement)
                    self.assertIsNone(passage.heading)

    def test_headings_set_the_heading_and_are_not_passages(self):
        passages = [p for p in split_passages(SOLAR) if p.source == "docs/warranty.md"]
        self.assertEqual([p.heading for p in passages], ["Panels", "Inverter", "Workmanship"])
        for passage in passages:
            self.assertNotIn("#", passage.text)
        self.assertNotIn("Warranty terms", " ".join(p.text for p in passages))

    def test_table_rows_are_split_and_header_is_left_out(self):
        rows = [p for p in split_passages(SOLAR) if p.source == "docs/price-list.md" and p.text.startswith("|")]
        self.assertEqual(len(rows), 3)
        self.assertIn("| 3 kW grid-tie | 4,20,000 | panels, inverter, mounting, installation |", [r.text for r in rows])
        for row in rows:
            self.assertNotIn("Price (NPR)", row.text)
            self.assertNotIn("---", row.text)
            self.assertEqual(row.heading, "Ghamghar Solar price list (fictional, for testing)")

    def test_long_paragraph_splits_at_sentences_into_substrings(self):
        text = (
            "The first sentence is here. The second sentence follows it. "
            "A third sentence comes next, and it is a little longer than the others. "
            "Short one. " + "x" * 150 + " tail words end it."
        )
        pack = pack_with_docs({"docs/long.md": text})
        passages = split_passages(pack, max_chars=80)
        self.assertGreater(len(passages), 2)
        for passage in passages:
            with self.subTest(text=passage.text[:30]):
                self.assertIn(passage.text, text)
                self.assertLessEqual(len(passage.text), 80)
        # Greedy packing: the first two sentences fit in 80 characters, so they share a piece.
        self.assertEqual(passages[0].text, "The first sentence is here. The second sentence follows it.")


class Search(unittest.TestCase):
    def test_inverter_warranty_question_finds_the_inverter_warranty(self):
        index = PassageIndex.from_pack(SOLAR)
        top = [p for p, _ in index.search("inverter ma pani 10 years warranty cha", k=2)]
        self.assertTrue(any(
            p.source in ("docs/warranty.md", "fact:inverter-warranty") and "inverter" in p.text.lower()
            for p in top
        ), [p.source for p in top])

    def test_mixed_language_price_question_matches_the_number_written_with_commas(self):
        index = PassageIndex.from_pack(SOLAR)
        top = [p for p, _ in index.search("3 kW grid-tie system installation sahit 4 lakh 20 hajar parcha", k=2)]
        self.assertTrue(any("4,20,000" in p.text for p in top), [p.text for p in top])

    def test_accounting_question_returns_the_accounting_passage_first(self):
        index = PassageIndex.from_pack(POS)
        results = index.search("direct integration cha accounting software sanga")
        self.assertIn("accounting", results[0][0].text)

    def test_empty_or_stopword_only_query_returns_nothing(self):
        index = PassageIndex.from_pack(SOLAR)
        for query in ("", "   ", "the a of", "ko ma cha ra ni"):
            with self.subTest(query=query):
                self.assertEqual(index.search(query), [])

    def test_results_are_capped_positive_and_sorted(self):
        index = PassageIndex.from_pack(SOLAR)
        results = index.search("warranty years price installation", k=3)
        self.assertLessEqual(len(results), 3)
        scores = [score for _, score in results]
        self.assertTrue(all(score > 0 for score in scores))
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(index.search("warranty", k=0), [])

    def test_ties_keep_original_order(self):
        passages = split_passages(pack_with_docs({"docs/a.md": "red apples.\n\nred apples."}))
        results = PassageIndex(passages).search("apples")
        self.assertEqual([p.source for p, _ in results], ["docs/a.md", "docs/a.md"])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(results[0][0].text, "red apples.")
        self.assertEqual(results[1][0].text, "red apples.")


class Tokens(unittest.TestCase):
    def test_thousands_separators_are_removed(self):
        self.assertEqual(tokenize("4,20,000"), ["420000"])
        self.assertEqual(tokenize("NPR 2,500"), ["npr", "2500"])
        self.assertEqual(tokenize("420000"), ["420000"])

    def test_lakh_and_hajar_become_an_integer_token(self):
        self.assertIn("420000", tokenize("4 lakh 20 hajar"))
        self.assertIn("400000", tokenize("4 lakh"))
        self.assertIn("20000", tokenize("20 hajar"))

    def test_devanagari_words_are_kept_whole(self):
        self.assertEqual(tokenize("शुक्रबार भोलि"), ["शुक्रबार", "भोलि"])

    def test_stopwords_are_dropped(self):
        self.assertEqual(tokenize("the price is in ko"), ["price"])


if __name__ == "__main__":
    unittest.main()
