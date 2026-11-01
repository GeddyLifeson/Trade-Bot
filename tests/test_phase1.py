"""Offline tests for Phase 1.  python -m unittest discover -s tests"""
import datetime as dt
import io
import math
import os
import random
import tempfile
import unittest
import zipfile
from unittest import mock

from tradebot import assay, sources, store


class TempData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(store, "DATA", self.tmp.name)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()


class StoreTest(TempData):
    def test_revision_is_appended_and_as_of_replays_the_old_value(self):
        store.write("bars", "x", "AAA", [{"date": "2026-01-02", "close": 10}], ["close"], "2026-01-03T00:00:00Z")
        self.assertEqual(store.write("bars", "x", "AAA", [{"date": "2026-01-02", "close": 10}], ["close"],
                                     "2026-01-04T00:00:00Z"), 0)   # unchanged -> nothing appended
        store.write("bars", "x", "AAA", [{"date": "2026-01-02", "close": 11}], ["close"], "2026-01-05T00:00:00Z")
        self.assertEqual(store.read("bars", "x", "AAA")[0]["close"], "11")
        self.assertEqual(store.read("bars", "x", "AAA", "2026-01-04T12:00:00Z")[0]["close"], "10")
        self.assertEqual(store.read("bars", "x", "AAA", "2026-01-02T23:00:00Z"), [])   # not yet fetched

    def test_windows_reserved_ticker_round_trips(self):
        store.write("bars", "x", "PRN", [{"date": "2026-01-02", "close": 1}], ["close"])
        self.assertEqual(store.keys("bars", "x"), {"PRN"})
        self.assertEqual(len(store.read("bars", "x", "PRN")), 1)


class CatalogueTest(unittest.TestCase):
    def test_vanished_symbol_is_kept_as_not_seen(self):
        cat = {}
        sources.merge_catalogue(cat, {"sec": [{"symbol": "OLD", "asset_class": "us_equity", "status": "active"},
                                              {"symbol": "NEW", "asset_class": "us_equity", "status": "active"}]}, "2026-01-01")
        sources.merge_catalogue(cat, {"sec": [{"symbol": "NEW", "asset_class": "us_equity", "status": "active"}]}, "2026-01-02")
        self.assertEqual(cat["OLD"]["status"], "not_seen")
        self.assertEqual(cat["NEW"]["status"], "active")

    def test_unlisted_source_does_not_mark_its_symbols_gone(self):
        cat = {}
        sources.merge_catalogue(cat, {"alpaca": [{"symbol": "SPY", "asset_class": "us_equity", "status": "active"}]}, "2026-01-01")
        sources.merge_catalogue(cat, {"sec": [{"symbol": "AAPL", "asset_class": "us_equity", "status": "active"}]}, "2026-01-02")
        self.assertEqual(cat["SPY"]["status"], "active")

    def test_alpaca_status_wins_over_sec(self):
        cat = {}
        sources.merge_catalogue(cat, {"alpaca": [{"symbol": "X", "asset_class": "us_equity", "status": "inactive"}],
                                      "sec": [{"symbol": "X", "asset_class": "us_equity", "status": "active"}]}, "2026-01-01")
        self.assertEqual(cat["X"]["status"], "inactive")
        self.assertEqual(cat["X"]["sources"], "alpaca;sec")


class ParseTest(TempData):
    def test_stooq_zip_loads_and_maps_tickers(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("data/daily/us/nyse stocks/1/brk-b.us.txt",
                       "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>\n"
                       "BRK-B.US,D,20260102,000000,400,410,399,405,1000,0\n")
            z.writestr("data/daily/uk/x.uk.txt", "ignored")
        path = os.path.join(store.DATA, "d.zip")
        with open(path, "wb") as f:
            f.write(buf.getvalue())
        self.assertEqual(sources.load_stooq_zip(path), (1, 1))
        self.assertEqual(store.read("bars", "stooq", "BRK.B")[0]["close"], "405")

    def test_forming_bars_are_dropped(self):
        today = sources.today().isoformat()
        rows = [{"date": "2020-01-01"}, {"date": today}]
        self.assertEqual(sources.final(rows), [{"date": "2020-01-01"}])

    def test_kraken_drops_its_forming_last_candle(self):
        t0 = int(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc).timestamp())
        payload = {"error": [], "result": {"XXBTZUSD": [[t0, "1", "2", "0.5", "1.5", "1", "9", 3],
                                                        [t0 + 86400, "1", "2", "0.5", "1.6", "1", "9", 3]], "last": 0}}
        with mock.patch.object(sources.pf, "get_json", return_value=payload):
            bars = sources.kraken_ohlc("XBTUSD")
        self.assertEqual([b["date"] for b in bars], ["2020-01-01"])

    def test_missing_key_is_reported_not_guessed(self):
        with mock.patch.dict(os.environ, {"POLYGON_API_KEY": ""}):
            with self.assertRaises(sources.MissingKey):
                sources.polygon_grouped("2026-01-02")


class AssayTest(TempData):
    def _walk(self, n, mu, sigma, seed=1):
        rng = random.Random(seed)
        p, out = 100.0, []
        for _ in range(n):
            p *= math.exp(rng.gauss(mu, sigma))
            out.append(p)
        return out

    def test_band_contains_the_true_sharpe(self):
        true_sr = 0.001 / 0.01 * math.sqrt(252)   # ~1.59
        hits = sum(1 for seed in range(40)
                   if (s := assay.score(self._walk(300, 0.001, 0.01, seed), 252))[1] <= true_sr <= s[2])
        self.assertGreaterEqual(hits, 34)   # ~95% coverage expected

    def test_short_or_flat_window_is_unrated(self):
        self.assertIsNone(assay.score(self._walk(200, 0, 0.01), 252))
        self.assertIsNone(assay.score([100.0] * 300, 252))

    def test_every_catalogue_symbol_is_listed_and_unrated_ones_say_why(self):
        start = sources.today() - dt.timedelta(400)
        closes = self._walk(400, 0.002, 0.01)
        store.write("bars", "coinbase", "BTC-USD",
                    [{"date": (start + dt.timedelta(i)).isoformat(), "open": c, "high": c, "low": c, "close": c, "volume": 1}
                     for i, c in enumerate(closes)], sources.BAR)
        store.write("bars", "stooq", "OLD", [{"date": "2019-01-02", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}], sources.BAR)
        cat = {s: {"asset_class": c, "status": "active"} for s, c in
               (("BTC-USD", "crypto"), ("OLD", "us_equity"), ("NONE", "us_equity"))}
        rows, _ = assay.run(cat)
        by = {r["symbol"]: r for r in rows}
        self.assertEqual(len(rows), 3)
        self.assertEqual(by["BTC-USD"]["rank"], 1)
        self.assertLess(by["BTC-USD"]["band_lo"], by["BTC-USD"]["score"])
        self.assertEqual(by["OLD"]["reason"], "stale")
        self.assertEqual(by["NONE"]["reason"], "no history")
        self.assertNotIn("score", by["NONE"])


if __name__ == "__main__":
    unittest.main()
