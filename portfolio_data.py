"""Load the original exercise snapshot into a fresh SQLite database."""

import csv
import io
import sqlite3
from pathlib import Path
from zipfile import ZipFile

ARCHIVE = Path(__file__).with_name("Exercise_PL_AI_Engineer.zip")
PREFIX = "Exercise_PL_AI_Engineer/"
TABLES = (
    "sectors", "securities", "benchmarks", "portfolios", "holdings",
    "transactions", "historical_prices", "portfolio_performance", "risk_metrics",
)
ANALYTIC_VIEWS = ("portfolio_values", "portfolio_sector_values", "portfolio_diversification")
QUERY_TABLES = TABLES + ANALYTIC_VIEWS

ANALYTICS_SQL = """
CREATE VIEW portfolio_values AS
SELECT p.portfolio_id, p.portfolio_name,
       SUM(h.quantity * s.current_price) AS total_value
FROM portfolios p JOIN holdings h USING (portfolio_id)
JOIN securities s USING (security_id)
GROUP BY p.portfolio_id, p.portfolio_name;

CREATE VIEW portfolio_sector_values AS
SELECT p.portfolio_id, p.portfolio_name, sec.sector_name,
       SUM(h.quantity * s.current_price) AS sector_value
FROM portfolios p JOIN holdings h USING (portfolio_id)
JOIN securities s USING (security_id)
JOIN sectors sec USING (sector_id)
GROUP BY p.portfolio_id, p.portfolio_name, sec.sector_id;

CREATE VIEW portfolio_diversification AS
SELECT p.portfolio_id, p.portfolio_name,
       COUNT(DISTINCT CASE WHEN s.asset_type = 'Stock' THEN s.sector_id END) AS sector_count,
       COUNT(h.holding_id) AS total_holdings,
       ROUND(CAST(COUNT(DISTINCT CASE WHEN s.asset_type = 'Stock' THEN s.sector_id END) AS REAL)
             / NULLIF(COUNT(h.holding_id), 0), 3) AS diversification_ratio
FROM portfolios p JOIN holdings h USING (portfolio_id)
JOIN securities s USING (security_id)
GROUP BY p.portfolio_id, p.portfolio_name;
"""


def exercise_text(path):
    with ZipFile(ARCHIVE) as archive:
        return archive.read(PREFIX + path).decode("utf-8-sig")


def load_database():
    connection = sqlite3.connect(":memory:", cached_statements=0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with ZipFile(ARCHIVE) as archive:
            connection.executescript(archive.read(PREFIX + "database_schema.sql").decode("utf-8-sig"))
            with connection:
                for table in TABLES:
                    raw = archive.read(f"{PREFIX}data/{table}.csv").decode("utf-8-sig")
                    header, *rows = csv.reader(io.StringIO(raw))
                    columns = [column["name"] for column in connection.execute(f"PRAGMA table_info({table})")]
                    if header != columns:
                        raise ValueError(f"Unexpected columns in {table}.csv")
                    placeholders = ", ".join("?" for _ in header)
                    connection.executemany(
                        f"INSERT INTO {table} VALUES ({placeholders})",
                        ([value if value != "" else None for value in row] for row in rows),
                    )
        connection.executescript(ANALYTICS_SQL)
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA query_only = ON")
        return connection
    except Exception:
        connection.close()
        raise


def schema_for_model(connection):
    # Database rows and evaluator fixtures are not part of the schema metadata.
    tables = []
    for table in QUERY_TABLES:
        columns = connection.execute(f"PRAGMA table_info({table})")
        signature = ", ".join(f"{column['name']} {column['type']}" for column in columns)
        tables.append(f"{table}({signature})")
    return "\n".join(tables)


def portfolio_details(connection, portfolio):
    return connection.execute(
        """
        SELECT p.target_risk_level, p.benchmark_index, pp.performance_date,
               pp.total_return_1m, pp.total_return_3m
        FROM portfolios p
        LEFT JOIN portfolio_performance pp ON pp.portfolio_id = p.portfolio_id
        WHERE p.portfolio_name = ?
        ORDER BY pp.performance_date DESC
        LIMIT 1
        """,
        (portfolio,),
    ).fetchone()


if __name__ == "__main__":
    from contextlib import closing

    with closing(load_database()) as database:
        for name in TABLES:
            count = database.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            print(f"{name}: {count} rows")
