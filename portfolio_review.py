"""Compare two allocation calculations using the supplied snapshot."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from portfolio_agent import AgentError


@dataclass
class AllocationComparison:
    holdings: list[dict]
    allocations: list[dict]
    total_position_value: Decimal
    currency: str


def compare_allocations(database, exposure):
    rows = database.execute(
        """
        SELECT s.symbol, h.quantity, s.current_price, s.currency
        FROM holdings h
        JOIN portfolios p USING (portfolio_id)
        JOIN securities s USING (security_id)
        WHERE p.portfolio_name = ?
        """,
        (exposure.portfolio,),
    )
    positions = {}
    currencies = set()
    for symbol, quantity, price, currency in rows:
        if quantity is None or price is None or not currency:
            raise AgentError("A supplied position is missing its quantity, price, or currency.")
        quantity, price = Decimal(str(quantity)), Decimal(str(price))
        if not quantity.is_finite() or not price.is_finite() or quantity < 0 or price < 0:
            raise AgentError("A supplied position has an invalid quantity or price.")
        positions[symbol] = (quantity, price)
        currencies.add(currency)
    if len(currencies) != 1:
        raise AgentError("The comparison requires one currency or an exchange-rate table.")
    total = sum((quantity * price for quantity, price in positions.values()), Decimal(0))
    if total <= 0:
        raise AgentError("The supplied position values do not have a positive total.")

    holdings = []
    category_values = defaultdict(Decimal)
    recorded_weights = defaultdict(Decimal)
    for holding in exposure.holdings:
        quantity, price = positions[holding.symbol]
        value = quantity * price
        rebuilt_weight = value / total
        category_values[holding.sector] += value
        recorded_weights[holding.sector] += holding.weight
        holdings.append({
            "symbol": holding.symbol, "company": holding.company, "allocation": holding.sector,
            "quantity": quantity, "price": price, "position_value": value,
            "recorded_weight": holding.weight, "rebuilt_weight": rebuilt_weight,
            "difference": rebuilt_weight - holding.weight,
        })

    allocations = [
        {
            "allocation": category, "recorded_weight": weight,
            "rebuilt_weight": category_values[category] / total,
            "difference": category_values[category] / total - weight,
        }
        for category, weight in recorded_weights.items()
    ]
    allocations.sort(key=lambda row: (-abs(row["difference"]), row["allocation"]))
    return AllocationComparison(holdings, allocations, total, next(iter(currencies)))


def review_note(exposure, comparison, details):
    lines = [
        f"# Portfolio review — {exposure.portfolio}", "",
        f"- Recorded equity allocation: {exposure.equity_weight:.2%}",
        f"- Recorded bond allocation: {exposure.bond_weight:.2%}",
        f"- Weight not represented in the snapshot: {exposure.unrepresented_weight:.2%}", "",
        "## Compare calculation bases", "",
        "Both columns describe the whole portfolio. Rebuilt weights divide each supplied "
        "quantity × price by the total value of the recorded positions.", "",
        "| Allocation | Recorded weight | Rebuilt weight | Difference in percentage points |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in comparison.allocations:
        lines.append(
            f"| {row['allocation']} | {row['recorded_weight']:.2%} | "
            f"{row['rebuilt_weight']:.2%} | {row['difference'] * 100:+.2f} |"
        )
    lines.extend([
        "", "Current holding and quote dates are absent from the supplied schema. "
        "A difference between these calculations needs a source check before either basis is used in a report.",
        "", f"Latest performance record: {details['performance_date'] or 'Unavailable'}.",
        "", "Sources: holdings.csv, securities.csv, sectors.csv, portfolios.csv, portfolio_performance.csv.",
    ])
    return "\n".join(lines)
