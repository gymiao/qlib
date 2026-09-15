from datetime import date
import unittest

from apps.quant_research.account import Account
from apps.quant_research.options import OptionContract


class AccountTest(unittest.TestCase):
    def test_trade_preserves_net_asset_value_before_fees(self):
        account = Account(10_000)
        account.trade_equity(date(2024, 1, 2), "SPY", 10, 400)
        self.assertAlmostEqual(account.mark_to_market({"SPY": 400}, {}), 10_000)

    def test_long_put_physical_expiry_sells_covered_shares(self):
        account = Account(20_000)
        account.trade_equity(date(2024, 1, 2), "SPY", 100, 100)
        contract = OptionContract("SPY-P-95", "SPY", date(2024, 2, 16), 95)
        account.trade_option(date(2024, 1, 2), contract, 1, 2)
        account.settle_expired(date(2024, 2, 16), {"SPY": 80})
        self.assertEqual(account.equities["SPY"].shares, 0)
        self.assertAlmostEqual(account.cash, 19_300)

    def test_cash_secured_put_reserve_cannot_be_reused(self):
        account = Account(10_000)
        first = OptionContract("A", "SPY", date(2024, 2, 16), 50)
        second = OptionContract("B", "QQQ", date(2024, 2, 16), 60)
        account.trade_option(date(2024, 1, 2), first, -1, 1)
        self.assertAlmostEqual(account.available_cash, 5_100)
        with self.assertRaisesRegex(ValueError, "cash-secured"):
            account.trade_option(date(2024, 1, 2), second, -1, 1)

    def test_covered_call_requires_shares_and_assigns_at_expiry(self):
        account = Account(20_000)
        account.trade_equity(date(2024, 1, 2), "SPY", 100, 100)
        call = OptionContract("SPY-C-105", "SPY", date(2024, 2, 16), 105, option_type="call")
        account.trade_option(date(2024, 1, 2), call, -1, 2)
        self.assertAlmostEqual(account.cash, 10_200)
        with self.assertRaisesRegex(ValueError, "uncover"):
            account.trade_equity(date(2024, 1, 3), "SPY", -1, 101)
        account.settle_expired(date(2024, 2, 16), {"SPY": 110})
        self.assertEqual(account.equities["SPY"].shares, 0)
        self.assertAlmostEqual(account.cash, 20_700)

    def test_naked_call_is_rejected(self):
        account = Account(20_000)
        call = OptionContract("SPY-C-105", "SPY", date(2024, 2, 16), 105, option_type="call")
        with self.assertRaisesRegex(ValueError, "covered call"):
            account.trade_option(date(2024, 1, 2), call, -1, 2)

    def test_equity_purchase_cannot_spend_cash_secured_put_reserve(self):
        account = Account(10_000)
        contract = OptionContract("A", "SPY", date(2024, 2, 16), 50)
        account.trade_option(date(2024, 1, 2), contract, -1, 1)
        with self.assertRaisesRegex(ValueError, "insufficient cash"):
            account.trade_equity(date(2024, 1, 2), "QQQ", 60, 100)

    def test_delta_is_included_in_factor_dollars(self):
        account = Account(20_000)
        account.trade_equity(date(2024, 1, 2), "QQQ", 100, 100)
        account.equities["QQQ"].factor_loadings = {"SPY": 1.1, "QQQ_RESIDUAL": 1.0}
        contract = OptionContract("Q", "QQQ", date(2024, 2, 16), 95)
        account.trade_option(date(2024, 1, 2), contract, 1, 2)
        account.options["Q"].delta = -0.5
        exposure = account.factor_dollars({"QQQ": 100})
        self.assertAlmostEqual(exposure["SPY"], 5_500)
        self.assertAlmostEqual(exposure["QQQ_RESIDUAL"], 5_000)


if __name__ == "__main__":
    unittest.main()
