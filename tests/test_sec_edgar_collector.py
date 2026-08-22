from datetime import date
import unittest

from collectors.sec_edgar_collector import (
    SECSource,
    build_ticker_cik_map,
    filing_url,
    parse_recent_filings,
)


class SECEdgarCollectorTests(unittest.TestCase):
    def test_build_ticker_cik_map_uses_sec_field_order(self) -> None:
        payload = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"]],
        }
        self.assertEqual(build_ticker_cik_map(payload), {"AAPL": "0000320193"})

    def test_filing_url_uses_unpadded_cik_and_compact_accession(self) -> None:
        self.assertEqual(
            filing_url("0000320193", "0000320193-26-000001", "form8-k.htm"),
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019326000001/form8-k.htm",
        )

    def test_parse_recent_filings_filters_date_and_form(self) -> None:
        payload = {
            "name": "Example Space Corp",
            "cik": "1234",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000001234-26-000001", "0000001234-26-000002"],
                    "filingDate": ["2026-08-21", "2026-08-21"],
                    "acceptanceDateTime": ["2026-08-21T14:30:00.000Z", ""],
                    "form": ["8-K", "4"],
                    "primaryDocument": ["event.htm", "ownership.xml"],
                    "primaryDocDescription": ["CURRENT REPORT", "FORM 4"],
                }
            },
        }
        articles = parse_recent_filings(
            payload,
            SECSource(2, "SEC EDGAR"),
            cutoff=date(2026, 8, 20),
            allowed_forms={"8-K"},
        )
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].external_id, "0000001234-26-000001")
        self.assertEqual(articles[0].title, "Example Space Corp files 8-K")
        self.assertEqual(articles[0].published_at, "2026-08-21T14:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
