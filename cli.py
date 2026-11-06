"""Ask the same portfolio agent from a terminal."""

import argparse
import csv
import sys

from portfolio_agent import (
    AgentError, Clarification, Exposure, GeminiRouter, PortfolioAgent, ReviewAnswer, demo_router, exposure_headline,
)
from settings import gemini_settings


def show_answer(answer):
    if isinstance(answer, ReviewAnswer):
        print(answer.text)
        for holding in answer.holdings:
            print(f"{holding.symbol}: {holding.company} — {holding.weight:.2%} of the portfolio")
        return
    if isinstance(answer, Clarification):
        print(answer.message)
        for option in answer.choices:
            print(f"  {option}")
        return
    writer = csv.writer(sys.stdout, lineterminator="\n")
    if isinstance(answer, Exposure):
        print(exposure_headline(answer))
        if answer.equity_weight:
            writer.writerow(["Sector", "Share of equities", "Share of whole portfolio"])
            for sector, weight in answer.sectors.items():
                writer.writerow([sector, f"{weight / answer.equity_weight:.2%}", f"{weight:.2%}"])
        return
    writer.writerow(answer.columns)
    writer.writerows(answer.rows)
    if answer.truncated:
        print("Output limited to the first 100 rows.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", help="Omit to enter questions interactively.")
    parser.add_argument("--demo", action="store_true", help="Use the limited local examples without Gemini.")
    args = parser.parse_args()
    key, model = gemini_settings()
    if not args.demo and not key:
        parser.error("set GEMINI_API_KEY in .env or use --demo for the sample questions")
    try:
        agent = PortfolioAgent(demo_router if args.demo else GeminiRouter(key, model))
    except AgentError as error:
        parser.error(str(error))
    if args.demo:
        print("Sample mode | limited local questions", file=sys.stderr)
    if args.question is not None:
        try:
            show_answer(agent.answer(args.question))
        except AgentError as error:
            print(error, file=sys.stderr)
            return 1
        return 0
    while True:
        try:
            question = input("Portfolio question (empty to quit): ")
            if not question.strip():
                return 0
            answer = agent.answer(question)
            if isinstance(answer, Clarification) and answer.choices:
                show_answer(answer)
                choice = input("Enter the full portfolio name: ").strip()
                if choice in answer.choices:
                    show_answer(agent.exposure_for(choice))
            else:
                show_answer(answer)
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        except AgentError as error:
            print(error, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
