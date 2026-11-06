import json
import unittest
from contextlib import closing
from decimal import Decimal
from unittest.mock import patch

from evaluate import check_case, expected_exposure, same_rows
from portfolio_agent import (
    COUNT_QUESTION, AgentError, Clarification, GeminiRouter, PortfolioAgent, ReviewAnswer,
    demo_router, execute_query, exposure_headline, portfolio_names,
)
from portfolio_data import exercise_text, load_database
from portfolio_review import compare_allocations, review_note


class PortfolioTests(unittest.TestCase):
    def test_every_supplied_portfolio_uses_its_own_holdings_and_context(self):
        agent = PortfolioAgent(lambda *_: self.fail("A simple review called Gemini."))
        with closing(load_database()) as database:
            names = portfolio_names(database)
            self.assertEqual(len(names), 13)
            for name in names:
                with self.subTest(portfolio=name):
                    expected = {
                        row[0]: Decimal(str(row[1]))
                        for row in database.execute("""
                            SELECT s.symbol, h.current_weight
                            FROM holdings h JOIN securities s USING (security_id)
                            JOIN portfolios p USING (portfolio_id)
                            WHERE p.portfolio_name = ?
                        """, (name,))
                    }
                    review = agent.answer("How are the holdings?", selected_portfolio=name)
                    self.assertEqual(review.portfolio, name)
                    self.assertEqual({h.symbol: h.weight for h in review.holdings}, expected)
                    exposure = agent.answer("What are the sector exposures for this portfolio?", selected_portfolio=name)
                    self.assertEqual(exposure.portfolio, name)
                    self.assertEqual(sum(h.weight for h in exposure.holdings), sum(expected.values()))
                    comparison = compare_allocations(database, exposure)
                    self.assertEqual({r["symbol"] for r in comparison.holdings}, set(expected))
                    picked = agent.answer(
                        "Pick any random one", selected_portfolio=name,
                        history=[{"question": "How are the holdings?", "answer": review}],
                    )
                    self.assertIn(picked.holdings[0].symbol, expected)
                    self.assertEqual(agent.model_calls, 0)

    def test_reported_conversation_uses_selected_portfolio_and_no_model_calls(self):
        def unexpected_call(*_):
            self.fail("Simple conversation unexpectedly called a model.")
        agent = PortfolioAgent(unexpected_call)
        history = []
        for question in (
            "so how is this potfolio in a scale of 1-10",
            "how are the holdings",
            "pick any random one",
        ):
            answer = agent.answer(question, selected_portfolio="Balanced Portfolio", history=history)
            self.assertIsInstance(answer, ReviewAnswer)
            self.assertEqual(answer.portfolio, "Balanced Portfolio")
            self.assertEqual(agent.model_calls, 0)
            history.append({"question": question, "answer": answer})
        self.assertIn("needs agreed criteria", history[0]["answer"].text)
        self.assertEqual(len(history[1]["answer"].holdings), 8)
        chosen = history[2]["answer"].holdings
        self.assertEqual(len(chosen), 1)
        self.assertIn(chosen[0], history[1]["answer"].holdings)

    def test_gemini_receives_selected_portfolio_and_recent_result_context(self):
        router = object.__new__(GeminiRouter)
        captured = []
        def plan(_router, question, schema, names, context):
            captured.append(context)
            return {"action": "sql", "sql": "SELECT COUNT(*) FROM securities", "portfolio": "", "message": ""}
        agent = PortfolioAgent(router)
        prior = agent.answer("How are the holdings?", selected_portfolio="Balanced Portfolio")
        with patch.object(GeminiRouter, "__call__", plan):
            result = agent.answer(
                "Which of those have quotes in USD?",
                selected_portfolio="Balanced Portfolio",
                history=[{"question": "How are the holdings?", "answer": prior}],
            )
        self.assertEqual(result.rows, [(30,)])
        self.assertEqual(agent.model_calls, 1)
        self.assertEqual(captured[0]["selected_portfolio"], "Balanced Portfolio")
        self.assertIn("AAPL", captured[0]["recent_exchanges"][0]["symbols"])

    def test_unknown_named_portfolio_is_not_replaced_by_selected_fund(self):
        agent = PortfolioAgent(lambda *_: {"action": "holdings", "portfolio": "Unknown Fund", "message": ""})
        result = agent.answer("Show holdings for Unknown Fund", selected_portfolio="Balanced Portfolio")
        self.assertIsInstance(result, Clarification)

    def test_portfolio_count_reads_data(self):
        self.assertEqual(PortfolioAgent(demo_router).answer(COUNT_QUESTION).rows, [(13,)])

    def test_balanced_portfolio_uses_two_denominators(self):
        exposure = PortfolioAgent(demo_router).exposure_for("Balanced Portfolio")
        self.assertEqual(exposure.sectors["Technology"], Decimal("0.22"))
        self.assertEqual(exposure.equity_weight, Decimal("0.36"))
        self.assertEqual(exposure.bond_weight, Decimal("0.64"))
        self.assertIn("61.11% of equities and 22.00% of the whole portfolio", exposure_headline(exposure))
        contributors = [holding.weight for holding in exposure.holdings if holding.sector == "Technology"]
        self.assertEqual(sum(contributors), Decimal("0.22"))

    def test_no_equities_and_unrepresented_weight(self):
        agent = PortfolioAgent(demo_router)
        bonds = agent.exposure_for("Fixed Income Plus")
        self.assertEqual(bonds.sectors, {})
        self.assertEqual(bonds.bond_weight, Decimal(1))
        partial = agent.exposure_for("Total Stock Market Index Fund")
        self.assertEqual(partial.unrepresented_weight, Decimal("0.02"))

    def test_ambiguous_portfolio_cannot_be_silently_selected(self):
        def router(*_):
            return {"action": "exposure", "portfolio": "international"}

        answer = PortfolioAgent(router).answer("Show sector exposures for international")
        self.assertIsInstance(answer, Clarification)
        self.assertEqual(answer.choices, ("International Equity Fund", "Total International Index Fund"))

    def test_expanded_model_name_requires_source_phrase(self):
        agent = PortfolioAgent(
            lambda *_: {"action": "exposure", "portfolio": "International Equity Fund"}, local_first=False,
        )
        result = agent.answer("Calculate the sector exposure breakdown for international equity")
        self.assertEqual(result.portfolio, "International Equity Fund")
        unrelated = agent.answer("Show the sector exposures for the Balanced Portfolio")
        self.assertIsInstance(unrelated, Clarification)

    def test_analytic_views_avoid_double_counting_positions(self):
        with closing(load_database()) as database:
            result = execute_query(database, """
                SELECT s.sector_value, p.total_value, ROUND(100.0 * s.sector_value / p.total_value, 2)
                FROM portfolio_sector_values s JOIN portfolio_values p USING (portfolio_id)
                WHERE p.portfolio_name = 'Balanced Portfolio' AND s.sector_name = 'Technology'
            """)
            self.assertEqual(result.rows, [(388704.0, 1073279.0, 36.22)])
            diversification = execute_query(database, """
                SELECT portfolio_name, sector_count, total_holdings, diversification_ratio
                FROM portfolio_diversification WHERE sector_count > 5
            """)
            self.assertEqual(diversification.rows, [("Total Stock Market Index Fund", 6, 12, 0.5)])

    def test_comparison_preserves_recorded_weights_and_rebuilds_values(self):
        exposure = PortfolioAgent(demo_router).exposure_for("Balanced Portfolio")
        with closing(load_database()) as database:
            comparison = compare_allocations(database, exposure)
        technology = next(row for row in comparison.allocations if row["allocation"] == "Technology")
        self.assertEqual(technology["recorded_weight"], Decimal("0.22"))
        self.assertAlmostEqual(float(technology["rebuilt_weight"]), 388704 / 1073279)
        self.assertAlmostEqual(sum(float(row["rebuilt_weight"]) for row in comparison.allocations), 1)
        self.assertEqual(exposure.sectors["Technology"], Decimal("0.22"))
        note = review_note(exposure, comparison, {"performance_date": "2023-10-09"})
        self.assertIn("36.22%", note)
        self.assertIn("holdings.csv", note)

    def test_comparison_rejects_unknown_currency_and_preserves_weight_gap(self):
        agent = PortfolioAgent(demo_router)
        partial = agent.exposure_for("Total Stock Market Index Fund")
        with closing(load_database()) as database:
            comparison = compare_allocations(database, partial)
            self.assertEqual(sum(row["recorded_weight"] for row in comparison.allocations), Decimal("0.98"))
            self.assertAlmostEqual(sum(float(row["rebuilt_weight"]) for row in comparison.allocations), 1)
            database.execute("PRAGMA query_only = OFF")
            database.execute("UPDATE securities SET currency = 'EUR' WHERE symbol = 'AAPL'")
            with self.assertRaises(AgentError):
                compare_allocations(database, partial)

    def test_sql_authorizer_rejects_unsafe_actions(self):
        attacks = [
            "DELETE FROM holdings",
            "PRAGMA query_only = OFF",
            "SELECT * FROM sqlite_master",
            "ATTACH DATABASE ':memory:' AS other",
            "SELECT load_extension('foo') FROM portfolios",
            "SELECT readfile('.env') FROM portfolios",
            "SELECT * FROM portfolios; DELETE FROM portfolios",
            "SELECT 999 AS portfolio_count",
            "SELECT SUM(a.volume + b.volume + c.volume) FROM historical_prices a "
            "CROSS JOIN historical_prices b CROSS JOIN historical_prices c",
        ]
        with closing(load_database()) as database:
            for sql in attacks:
                with self.subTest(sql=sql), self.assertRaises(AgentError):
                    execute_query(database, sql)
            self.assertEqual(execute_query(database, "SELECT COUNT(*) FROM portfolios").rows, [(13,)])
            self.assertEqual(execute_query(database, "SELECT COUNT(*) FROM holdings").rows, [(95,)])
            self.assertEqual(database.execute("PRAGMA query_only").fetchone()[0], 1)

    def test_output_row_cap(self):
        with closing(load_database()) as database:
            result = execute_query(database, "SELECT price_id FROM historical_prices ORDER BY price_id")
        self.assertEqual(len(result.rows), 100)
        self.assertTrue(result.truncated)

    def test_reference_queries_pass_engine_and_evaluator(self):
        # This verifies the tool/evaluator wiring with known plans, not Gemini accuracy.
        cases = json.loads(exercise_text("ground_truth_dataset.json"))["questions"]
        with closing(load_database()) as database:
            for case in cases:
                reference = case["ground_truth"]
                if case["type"] == "text2sql":
                    decision = {"action": "sql", "sql": reference["sql_query"]}
                else:
                    portfolio = "international equity" if case["id"] == 10 else reference["parameters"]["portfolio_name"]
                    decision = {"action": "exposure", "portfolio": portfolio}
                agent = PortfolioAgent(lambda *_: decision, local_first=False)
                with self.subTest(case=case["id"]):
                    self.assertIsNone(check_case(agent, database, case))

    def test_evaluator_rejects_incorrect_values_even_when_shape_matches(self):
        case = json.loads(exercise_text("ground_truth_dataset.json"))["questions"][0]
        agent = PortfolioAgent(lambda *_: {"action": "sql", "sql": "SELECT COUNT(*) + 1 FROM portfolios"}, local_first=False)
        with closing(load_database()) as database:
            self.assertIsNotNone(check_case(agent, database, case))
        self.assertFalse(same_rows([(1,), (1,)], [(1,), (2,)]))
        self.assertEqual(expected_exposure("Balanced Portfolio")["Technology"], Decimal("0.22"))


if __name__ == "__main__":
    unittest.main()
