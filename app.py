"""Browser interface for the portfolio analytics take-home."""

from contextlib import closing

import altair as alt
import pandas as pd
import streamlit as st

from portfolio_agent import (
    MAX_QUESTION,
    AgentError, Clarification, Exposure, GeminiRouter, PortfolioAgent, QueryResult, ReviewAnswer,
    demo_router, exposure_headline, portfolio_names,
)
from portfolio_data import load_database, portfolio_details
from portfolio_review import compare_allocations, review_note
from settings import gemini_settings

EQUITIES = "Equity holdings"
PORTFOLIO = "Whole portfolio"
UNREPRESENTED = "Weight not in snapshot"
ALL_HOLDINGS = "All holdings"
SECTOR_COLORS = ["#167562", "#2b5ea9", "#986127", "#8653aa", "#c04156",
                 "#166a8f", "#68842e", "#ad5440", "#444d9e", "#725f4e", "#8d4077"]


def chart_for_exposure(exposure):
    rows = []
    for index, (sector, weight) in enumerate(exposure.sectors.items()):
        if weight:
            rows.extend([
                {"basis": EQUITIES, "sector": sector, "share": float(weight / exposure.equity_weight), "order": index},
                {"basis": PORTFOLIO, "sector": sector, "share": float(weight), "order": index},
            ])
    for order, (label, weight) in enumerate(
        [("Bonds", exposure.bond_weight), (UNREPRESENTED, exposure.unrepresented_weight)],
        start=len(exposure.sectors),
    ):
        if weight:
            rows.append({"basis": PORTFOLIO, "sector": label, "share": float(weight), "order": order})

    categories = [sector for sector, weight in exposure.sectors.items() if weight]
    colors = SECTOR_COLORS[:len(categories)]
    for label, color in [("Bonds", "#637486"), (UNREPRESENTED, "#a4aeb7")]:
        if any(row["sector"] == label for row in rows):
            categories.append(label)
            colors.append(color)

    pick = alt.selection_point(name="sector_pick", fields=["sector"], toggle=False)
    return (
        alt.Chart(alt.Data(values=rows), description="Sector shares of equity holdings and of the whole portfolio.")
        .mark_bar(size=30, cursor="pointer")
        .encode(
            x=alt.X("share:Q", stack="zero", scale=alt.Scale(domain=[0, 1]),
                    axis=alt.Axis(format=".0%", title=None, tickCount=5)),
            y=alt.Y("basis:N", sort=[EQUITIES, PORTFOLIO], title=None),
            order=alt.Order("order:Q"),
            color=alt.Color("sector:N", scale=alt.Scale(domain=categories, range=colors),
                            legend=alt.Legend(title=None, orient="bottom", columns=2)),
            opacity=alt.condition(pick, alt.value(1), alt.value(0.72)),
            tooltip=[alt.Tooltip("basis:N", title="Percentage basis"), alt.Tooltip("sector:N", title="Allocation"),
                     alt.Tooltip("share:Q", title="Share", format=".2%")],
        )
        .add_params(pick)
        .configure(font="Arial")
        .properties(height=165)
    )


def render_exposure(exposure, request_id, browse=False):
    if not browse:
        st.subheader(exposure.portfolio)
    st.write(exposure_headline(exposure))
    if not exposure.holdings:
        return
    choices = [ALL_HOLDINGS] if browse else []
    choices.extend(sector for sector, weight in exposure.sectors.items() if weight)
    if exposure.bond_weight:
        choices.append("Bonds")
    if exposure.unrepresented_weight:
        choices.append(UNREPRESENTED)

    picker_key = f"holdings-{request_id}"
    if exposure.equity_weight or exposure.bond_weight:
        st.markdown("#### Sector allocation")
        st.caption(
            f"Equities account for {exposure.equity_weight:.2%} of this portfolio. "
            "Select a chart segment to see what contributes to it."
        )
        chart_key = f"allocation-{request_id}"

        def select_segment():
            selected = st.session_state[chart_key]["selection"].get("sector_pick", [])
            if selected and selected[0]["sector"] in choices:
                st.session_state[picker_key] = selected[0]["sector"]

        st.altair_chart(
            chart_for_exposure(exposure), width="stretch", height=195, key=chart_key,
            on_select=select_segment, selection_mode="sector_pick",
        )
    if not choices:
        return
    sector = st.selectbox("Inspect an allocation", choices, key=picker_key)
    if sector == UNREPRESENTED:
        st.info(f"{exposure.unrepresented_weight:.2%} is unrepresented in the supplied holding weights.")
    else:
        holdings = [h for h in exposure.holdings if sector == ALL_HOLDINGS or h.sector == sector]
        weight = sum(holding.weight for holding in holdings)
        holding_label = "holding" if len(holdings) == 1 else "holdings"
        heading = "What this portfolio owns" if sector == ALL_HOLDINGS else f"{sector} holdings"
        st.markdown(f"#### {heading}")
        st.caption(f"{len(holdings)} {holding_label} · {weight:.2%} of the whole portfolio")
        st.dataframe(
            [{"Symbol": h.symbol, "Holding": h.company, "Allocation": h.sector,
              "Share of whole portfolio (%)": float(h.weight * 100)}
             for h in holdings],
            hide_index=True,
            column_config={"Share of whole portfolio (%)": st.column_config.NumberColumn(format="%.2f")},
        )
    with st.expander("How this was calculated"):
        st.write(
            "We sum the supplied holding weights by equity sector. The equity-only percentages divide "
            "each sector's weight by the total equity weight. Whole-portfolio percentages use each "
            "sector's recorded portfolio weight directly. Bonds are shown separately."
        )
        if exposure.equity_weight:
            st.dataframe(
                [{"Sector": name, "Share of equities": f"{weight / exposure.equity_weight:.2%}",
                  "Share of whole portfolio": f"{weight:.2%}"}
                 for name, weight in exposure.sectors.items()],
                hide_index=True,
            )


def render_query(result):
    if not result.rows:
        st.info("No matching rows in the supplied data.")
    elif len(result.rows) == len(result.columns) == 1:
        label = result.columns[0].replace("_", " ").strip().capitalize()
        st.text(label)
        value = result.rows[0][0]
        if value is None:
            st.subheader("No value available", anchor=False)
        elif isinstance(value, (float, int)):
            formatted = f"{value:,.6f}".rstrip("0").rstrip(".") if isinstance(value, float) else f"{value:,}"
            st.subheader(formatted, anchor=False)
        else:
            st.text(str(value))
    else:
        frame = pd.DataFrame(result.rows)
        labels = [column.replace("_", " ").strip().capitalize() for column in result.columns]
        frame.columns = [f"{label} ({i + 1})" if labels.count(label) > 1 else label
                         for i, label in enumerate(labels)]
        st.dataframe(frame, hide_index=True)
    if result.truncated:
        st.caption("Showing the first 100 rows.")
    with st.expander("How this was calculated"):
        st.code(result.sql, language="sql")


def get_gemini_router(key, model):
    connection = st.session_state.get("gemini_connection")
    # Streamlit can reload the agent module while retaining a router from the old module.
    # Its exceptions then have a different class identity from the current AgentError.
    if connection is None or connection[:2] != (key, model) or type(connection[2]) is not GeminiRouter:
        connection = (key, model, GeminiRouter(key, model))
        st.session_state.gemini_connection = connection
    return connection[2]


def render_portfolio_details(details, compact=False):
    risk = details["target_risk_level"] or "Unavailable"
    benchmark = details["benchmark_index"] or "Unavailable"
    if compact:
        st.caption(f"Target risk: {risk} · Listed benchmark: {benchmark}")
        if details["performance_date"]:
            returns = []
            for label, field in (("1 month", "total_return_1m"), ("3 months", "total_return_3m")):
                value = details[field]
                returns.append(f"{label}: {value:.2f}%" if value is not None else f"{label}: unavailable")
            st.caption(f"Recorded returns as of {details['performance_date']} · {' · '.join(returns)}")
        else:
            st.caption("This portfolio has no performance records in the supplied data.")
        return
    st.markdown("#### Risk and recorded performance")
    st.write(f"Target risk: **{risk}** · Listed benchmark: **{benchmark}**")
    if not details["performance_date"]:
        st.caption("This portfolio has no performance records in the supplied data.")
        return
    st.caption(f"Latest performance record in the supplied data: {details['performance_date']}")
    columns = st.columns(2)
    for column, label, field in zip(
        columns, ("1-month return", "3-month return"), ("total_return_1m", "total_return_3m")
    ):
        value = details[field]
        column.metric(label, "Unavailable" if value is None else f"{value:.2f}%")


def render_comparison(exposure, details):
    st.subheader("Check the numbers")
    st.write(
        "Two reports can show different allocations when they use different calculation bases. "
        "Compare the recorded holding weights with a calculation using the supplied quantities and prices."
    )
    if exposure.unrepresented_weight:
        st.info(f"{exposure.unrepresented_weight:.2%} is absent from the recorded weights. Its value is unknown.")
    try:
        with closing(load_database()) as database:
            comparison = compare_allocations(database, exposure)
    except AgentError as error:
        st.info(str(error))
        return
    biggest = comparison.allocations[0]
    st.write(
        f"The **{biggest['allocation']}** allocation has the largest difference. Its recorded weight is "
        f"**{biggest['recorded_weight']:.2%}**. Rebuilding the weights gives "
        f"**{biggest['rebuilt_weight']:.2%}**, a difference of "
        f"**{biggest['difference'] * 100:+.2f} percentage points**."
    )
    chart_rows = []
    for row in comparison.allocations:
        chart_rows.extend([
            {"Allocation": row["allocation"], "Calculation": "Recorded weights", "Share": float(row["recorded_weight"])},
            {"Allocation": row["allocation"], "Calculation": "Quantity × price", "Share": float(row["rebuilt_weight"])},
        ])
    chart = (
        alt.Chart(alt.Data(values=chart_rows))
        .mark_bar()
        .encode(
            x=alt.X("Share:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format=".0%", title="Share of whole portfolio")),
            y=alt.Y("Allocation:N", sort=[row["allocation"] for row in comparison.allocations], title=None),
            yOffset="Calculation:N",
            color=alt.Color("Calculation:N", scale=alt.Scale(range=["#167562", "#cb843a"]),
                            legend=alt.Legend(title=None, orient="bottom")),
            tooltip=["Allocation:N", "Calculation:N", alt.Tooltip("Share:Q", format=".2%")],
        )
        .configure(font="Arial")
    )
    st.altair_chart(chart, width="stretch", height=max(215, len(comparison.allocations) * 48))
    st.caption(
        "The rebuilt percentages sum to 100% of the recorded positions. "
        "Holding and quote dates are absent from this snapshot. These differences need a source check."
    )
    selected = st.selectbox(
        "Which holdings explain the difference?",
        [row["allocation"] for row in comparison.allocations],
        key=f"comparison-{exposure.portfolio}",
    )
    st.dataframe(
        [{
            "Symbol": row["symbol"], "Quantity": float(row["quantity"]),
            f"Supplied price ({comparison.currency})": float(row["price"]),
            "Recorded share (%)": float(row["recorded_weight"] * 100),
            "Rebuilt share (%)": float(row["rebuilt_weight"] * 100),
            "Difference (pp)": float(row["difference"] * 100),
        } for row in comparison.holdings if row["allocation"] == selected],
        hide_index=True,
        column_config={
            "Recorded share (%)": st.column_config.NumberColumn(format="%.2f"),
            "Rebuilt share (%)": st.column_config.NumberColumn(format="%.2f"),
            "Difference (pp)": st.column_config.NumberColumn(format="%+.2f"),
        },
    )
    with st.expander("Calculation and source details"):
        st.write(
            "Recorded weights come from holdings.current_weight. Rebuilt weights use "
            "holding quantity × security current_price, divided by the total of those position values. "
            "Equities are grouped by sector and bonds are kept in a separate allocation."
        )
        st.code("rebuilt_share = (quantity × supplied_price) / sum(all recorded position values)", language=None)
    st.download_button(
        "Download review note", data=review_note(exposure, comparison, details),
        file_name="portfolio_review.md", mime="text/markdown",
    )


def render_chat(agent, portfolio):
    st.subheader("Ask about this portfolio")
    if agent.router is demo_router:
        st.caption("Local mode supports portfolio reviews, holdings, counts, and sector exposure.")
    else:
        st.caption("Simple questions use local calculations. Gemini handles other requests using the selected fund and recent conversation.")
    conversation = st.container(height=420)
    question = st.chat_input("Ask a portfolio question", max_chars=MAX_QUESTION)
    histories = st.session_state.setdefault("portfolio_chats", {})
    history = histories.setdefault(portfolio, [])
    if question:
        request_id = st.session_state.get("request_id", 0) + 1
        st.session_state.request_id = request_id
        turn = {"id": request_id, "question": question, "answer": None, "error": None}
        with st.spinner("Checking the portfolio snapshot…"):
            try:
                turn["answer"] = agent.answer(question, selected_portfolio=portfolio, history=history)
            except AgentError as error:
                turn["error"] = str(error)
        turn.update(source=agent.last_source, model_calls=agent.model_calls, tokens=agent.last_tokens)
        history.append(turn)
        del history[:-10]

    with conversation:
        if not history:
            st.write("Ask about holdings, sector allocation, or recorded returns.")
            st.caption(f"“This portfolio” refers to {portfolio}.")
        for turn in history:
            render_turn(agent, turn)


def render_turn(agent, turn):
    with st.chat_message("user", avatar=":material/person:"):
        st.text(turn["question"])
    with st.chat_message("assistant", avatar=":material/analytics:"):
        if "source" in turn:
            trace = f"{turn['source']} · {turn['model_calls']} model call(s)"
            if turn.get("tokens") is not None:
                trace += f" · {turn['tokens']} provider-reported tokens"
            st.caption(trace)
        if turn["error"]:
            st.error(turn["error"])
            return
        answer = turn["answer"]
        request_id = turn["id"]
        if isinstance(answer, Clarification):
            st.write(answer.message)
            if answer.choices:
                choice = st.selectbox("Portfolio", answer.choices, index=None, key=f"portfolio-{request_id}")
                if choice:
                    try:
                        turn["answer"] = agent.exposure_for(choice)
                        st.rerun()
                    except AgentError as error:
                        st.error(str(error))
        elif isinstance(answer, ReviewAnswer):
            st.write(answer.text)
            if answer.holdings:
                st.dataframe(
                    [{"Symbol": h.symbol, "Holding": h.company, "Allocation": h.sector,
                      "Portfolio weight (%)": float(h.weight * 100)} for h in answer.holdings],
                    hide_index=True,
                    column_config={"Portfolio weight (%)": st.column_config.NumberColumn(format="%.2f")},
                )
        elif isinstance(answer, Exposure):
            render_exposure(answer, request_id)
        elif isinstance(answer, QueryResult):
            render_query(answer)
        else:
            st.info("Submit the question again to refresh this answer.")


def main():
    st.set_page_config(page_title="Portfolio Review", page_icon="📊", layout="wide")
    st.html("""
        <style>
        .stMainBlockContainer {max-width: 94rem; padding-top: 2.4rem;}
        h1 {font-size: 2rem !important; font-weight: 600 !important; letter-spacing: -0.025em;}
        h3 {font-size: 1.25rem !important; font-weight: 600 !important;}
        h4 {font-size: 1.05rem !important; font-weight: 600 !important;}
        [data-testid="stMetricValue"] {font-variant-numeric: tabular-nums;}
        </style>
    """)
    st.caption("PORTFOLIO ANALYTICS")
    st.title("Portfolio Review")
    with closing(load_database()) as database:
        names = portfolio_names(database)
        st.write(f"Review {len(names)} supplied portfolios, inspect the holdings, and check the calculations.")
        portfolio = st.selectbox("Choose a portfolio", names, index=names.index("Balanced Portfolio"))
        details = portfolio_details(database, portfolio)
    st.caption("Supplied sample data · Portfolio holdings and prices have no recorded snapshot date")
    key, model = gemini_settings()
    overview, questions = st.columns([1.4, 1], gap="large")
    with overview:
        try:
            exposure = PortfolioAgent(demo_router).exposure_for(portfolio)
            summary = st.columns(3)
            summary[0].metric("Holdings", len(exposure.holdings))
            summary[1].metric("Equity allocation", f"{exposure.equity_weight:.2%}")
            summary[2].metric("Bond allocation", f"{exposure.bond_weight:.2%}")
            render_portfolio_details(details, compact=True)
            allocations, checks = st.tabs(["Allocation & holdings", "Check the numbers"])
            with allocations:
                render_exposure(exposure, f"browse-{portfolio}", browse=True)
            with checks:
                render_comparison(exposure, details)
        except AgentError as error:
            st.error(str(error))
    with questions:
        router = demo_router
        use_local = key and st.toggle(
            "Use local mode",
            help="Answer supported portfolio questions without calling Gemini.",
        )
        if key and not use_local:
            try:
                router = get_gemini_router(key, model)
            except AgentError as error:
                st.warning(str(error))
        render_chat(PortfolioAgent(router), portfolio)


if __name__ == "__main__":
    main()
