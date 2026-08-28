from __future__ import annotations

import unittest
from pathlib import Path

from teramont_monitor.html import extract_detail, extract_links
from teramont_monitor.sources import load_source_configs


FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = Path(__file__).parents[1] / "config" / "sources.json"


class HtmlExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.configs = {config.name: config for config in load_source_configs(CONFIG)}

    def fixture(self, name: str) -> str:
        return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")

    def test_extracts_expected_link_shape_for_each_source(self) -> None:
        expected = {
            "autoru": ("1131273493-a5e33347", "https://auto.ru/cars/new/group/volkswagen/teramont/24068818/24072830/1131273493-a5e33347"),
            "drom": ("460325385", "https://auto.drom.ru/moscow/volkswagen/teramont/460325385.html"),
            "avito": ("7957046084", "https://www.avito.ru/moskva/avtomobili/volkswagen_teramont_pro_2.0_amt_2026_7957046084"),
            "mashina": ("6a8ef6d8ed395bdd959aa66e", "https://m.mashina.kg/details/volkswagen-teramont-pro-6a8ef6d8ed395bdd959aa66e"),
            "kolesa": ("227501940", "https://kolesa.kz/a/show/227501940"),
        }
        for name, wanted in expected.items():
            with self.subTest(source=name):
                self.assertEqual(extract_links(self.fixture(name), self.configs[name]), [wanted])

    def test_rejects_disallowed_host_and_catalog_links(self) -> None:
        links = extract_links(self.fixture("autoru"), self.configs["autoru"])
        self.assertEqual(len(links), 1)
        self.assertNotIn("evil.example", links[0][1])

    def test_malformed_url_candidate_does_not_abort_valid_links(self) -> None:
        page = '<a href="//[broken">bad</a>' + self.fixture("drom")
        self.assertEqual(
            extract_links(page, self.configs["drom"]),
            [("460325385", "https://auto.drom.ru/moscow/volkswagen/teramont/460325385.html")],
        )

    def test_extract_detail_combines_visible_text_meta_and_jsonld(self) -> None:
        html = """
        <html><head><meta name="description" content="Автомобиль в наличии">
        <script type="application/ld+json">{"name":"Volkswagen Teramont Pro 2026","offers":{"price":5999000,"priceCurrency":"RUB"}}</script>
        <script>SECRET_SHOULD_NOT_APPEAR</script></head>
        <body><h1>Peak, black on black, DCC</h1></body></html>
        """
        text, metadata = extract_detail(html)
        self.assertIn("Автомобиль в наличии", text)
        self.assertIn("Peak, black on black, DCC", text)
        self.assertNotIn("SECRET_SHOULD_NOT_APPEAR", text)
        self.assertEqual(metadata["title"], "Volkswagen Teramont Pro 2026")
        self.assertEqual(metadata["price"], 5_999_000)
        self.assertEqual(metadata["price_currency"], "RUB")

    def test_primary_listing_content_excludes_related_cards_and_footer(self) -> None:
        html = """
        <html><body>
        <main><h1>Volkswagen Teramont Pro 2026</h1><p>Цвет кузова: белый. Без DCC.</p></main>
        <aside>Похожий автомобиль: Peak, black on black, DCC, в наличии</aside>
        <footer>Реклама: Summit, чёрный салон, физически в наличии</footer>
        </body></html>
        """

        text, _ = extract_detail(html)

        self.assertIn("Цвет кузова: белый", text)
        self.assertNotIn("Похожий автомобиль", text)
        self.assertNotIn("Реклама", text)


if __name__ == "__main__":
    unittest.main()
