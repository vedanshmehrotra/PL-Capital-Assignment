"""Run the exercise cases through Gemini and compare structured tool results."""

import csv
import io
import json
import math
from contextlib import closing
from decimal import Decimal

from portfolio_agent import AgentError, Exposure, GeminiRouter, PortfolioAgent, ProviderError, QueryResult
from portfolio_data import exercise_text, load_database
from settings import gemini_settings


def same_value(left, right):
    if isinstance(left, (float, int)) and isinstance(right, (float, int)):
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-6)
    return left == right


def same_rows(actual, expected, ordered=False):
    def same_row(left, right):
        return len(left) == len(right) and all(same_value(a, b) for a, b in zip(left, right))

    if len(actual) != len(expected):
        return False
    if ordered:
        return all(same_row(left, right) for left, right in zip(actual, expected))
    unmatched = list(actual)
    for target in expected:
        for index, row in enumerate(unmatched):
            if same_row(row, target):
                unmatched.pop(index)
                break
        else:
            return False
    return True


def expected_exposure(portfolio):
    def rows(table):
        return csv.DictReader(io.StringIO(exercise_text(f"data/{table}.csv")))

    portfolio_id = next(row["portfolio_id"] for row in rows("portfolios") if row["portfolio_name"] == portfolio)
    sector_names = {row["sector_id"]: row["sector_name"] for row in rows("sectors")}
    stocks = {
        row["security_id"]: sector_names.get(row["sector_id"], "Unclassified equities")
        for row in rows("securities")
        if row["asset_type"] == "Stock"
    }
    result = {}
    for row in rows("holdings"):
        if row["portfolio_id"] == portfolio_id and row["security_id"] in stocks:
            sector = stocks[row["security_id"]]
            result[sector] = result.get(sector, Decimal(0)) + Decimal(row["current_weight"])
    return result


def check_case(agent, reference_db, case):
    reference = case["ground_truth"]
    answer = agent.answer(case["question"])
    if case["type"] == "text2sql":
        if not isinstance(answer, QueryResult):
            return "Expected the SQL tool."
        expected = [tuple(row) for row in reference_db.execute(reference["sql_query"])]
        ranked = case["question"].casefold().startswith("show me the top ")
        if answer.truncated or not same_rows(answer.rows, expected, ordered=ranked):
            expected_width = len(expected[0]) if expected else len(answer.columns)
            return (
                f"SQL result differs from reference "
                f"({len(answer.rows)} rows × {len(answer.columns)} columns vs "
                f"{len(expected)} rows × {expected_width} columns)."
            )
    elif case["type"] == "exposure_calculator":
        if not isinstance(answer, Exposure):
            return "Expected the exposure calculator."
        portfolio = reference["parameters"]["portfolio_name"]
        expected = expected_exposure(portfolio)
        if (
            answer.portfolio != portfolio
            or answer.sectors != expected
            or answer.equity_weight != sum(expected.values(), Decimal(0))
        ):
            return "Portfolio or equity exposure weights differ from the reference."
    else:
        raise ValueError(f"Unknown evaluation type: {case['type']}")
    return None


def main():
    key, model = gemini_settings()
    if not key:
        print("Set GEMINI_API_KEY in .env to run the live evaluation.")
        return 2
    try:
        # Keep this benchmark Gemini-only so local routing cannot mask model failures.
        agent = PortfolioAgent(GeminiRouter(key, model), local_first=False)
    except AgentError as error:
        print(error)
        return 2
    cases = json.loads(exercise_text("ground_truth_dataset.json"))["questions"]
    passed = 0
    unavailable = 0
    with closing(load_database()) as reference_db:
        for case in cases:
            try:
                failure = check_case(agent, reference_db, case)
            except ProviderError as error:
                unavailable += 1
                print(f"{case['id']:2} UNAVAILABLE | {case['question']} | {error}", flush=True)
                continue
            except AgentError as error:
                failure = str(error)
            passed += failure is None
            outcome = f"FAIL — {failure}" if failure else "PASS"
            print(f"{case['id']:2} {outcome} | {case['question']}", flush=True)
    failed = len(cases) - passed - unavailable
    print(f"\n{passed} passed, {failed} failed, {unavailable} unavailable (Gemini-only; model: {agent.router.model}).")
    return int(passed != len(cases))


if __name__ == "__main__":
    raise SystemExit(main())
