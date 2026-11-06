# Portfolio Review

Portfolio analytics take-home with a browser interface, a terminal interface, and an evaluator. The three entry points use the same Python agent and the original SQLite schema and CSV files in `Exercise_PL_AI_Engineer.zip`.

## Run it

Use Python 3.11 or newer. Keep the exercise ZIP beside the Python files.

The direct dependencies are pinned to the versions used for the submission checks. The interface and charts use Arial, with Consolas for query code.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe portfolio_data.py
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the URL Streamlit prints. Choose a supplied portfolio to see its allocation, recorded performance, and holdings alongside the question panel. The exercise ZIP supplies the data, and the app loads it into read-only SQLite for each lookup. “This portfolio” in a question refers to the selected fund. Each portfolio keeps its last ten exchanges in the current browser session.

Simple counts, general portfolio reviews, holdings requests, and sector exposures are answered locally. Other requests go to Gemini with the selected portfolio and the last four exchanges. The context contains prior questions, relevant portfolio/symbol names, and up to five prior result rows. Each reply labels its route, number of model calls, and provider-reported tokens when available. The local route makes no Gemini request. The displayed model-call count excludes network retry attempts.

Try “How are the holdings?” followed by “Pick any random one.” The second question selects a holding from the preceding portfolio review. A request for a 1–10 rating explains that a score needs agreed criteria and supplies measured facts instead of an arbitrary grade.

On Windows, run Streamlit with `.\.venv\Scripts\python.exe -m streamlit run app.py` from this folder so it uses the same environment where the dependencies were installed. If the app reports that the Gemini SDK could not load, check which URL the terminal printed and open that copy.

For free-form questions, copy `.env.example` to `.env` if the file is missing and paste your Google AI Studio key after `GEMINI_API_KEY=`. Keep `.env` local. The browser, CLI, and evaluator all read it. Process environment variables take precedence. The default model is `gemini-3.5-flash-lite`; set `GEMINI_MODEL` to use another model available to your key.

The Gemini client retries eligible server errors once. If Gemini remains unavailable, turn on **Use local mode** to keep supported local questions available. The question panel accepts typed questions and has no preset prompt buttons.

To check that Gemini is answering beyond the examples, open **Ask a question** and try “What is the latest recorded 3-month return and its date for the Balanced Portfolio?” The supplied data records **9.60% on 2023-10-09**. Then ask the same question about the Fixed Income Plus portfolio, which has no performance rows.

## Terminal and evaluation

The evaluator explicitly disables local-first routing. Its results measure Gemini-generated tool decisions separately from the hybrid behavior used by the browser.

```powershell
.\.venv\Scripts\python.exe cli.py --demo "How many portfolios do we have in total?"
.\.venv\Scripts\python.exe cli.py "What are the names of all active portfolios?"
.\.venv\Scripts\python.exe evaluate.py
.\.venv\Scripts\python.exe -m unittest -v test_portfolio
```

Omit the question to use the interactive CLI. The second and third commands require `GEMINI_API_KEY`. The evaluator runs all ten supplied questions through Gemini and checks the actual results. It executes the supplied SQL separately and derives the expected exposure weights independently from the CSVs. It compares values with numeric tolerance and allows row reordering unless the question explicitly requests a ranking. SQL columns are compared in reference order. Provider errors are reported separately as **UNAVAILABLE**. The local tests exercise known tool decisions and guardrails.

## Demo and calculation

The desk opens on the Balanced Portfolio. Technology is 61.11% of its equity holdings and 22.00% of the whole portfolio. Select a segment in either bar to see the contributing holdings. The allocation selector offers the same drill-down by keyboard and lets you return to **All holdings**. Holding weights are numeric so the table can sort them. Expand **How this was calculated** for exact sector percentages and the calculation method.

The calculator uses the recorded holding weights. It sums stock weights by sector and divides by total stock weight for the equity-only view. The whole-portfolio view uses recorded weights directly and shows bonds separately. Any weight absent from the snapshot stays explicitly unrepresented. The supplied data describes a sample snapshot with no live quotes.

The browser displays the fund's recorded target risk, listed benchmark, and most recent available one- and three-month returns with their source date. Some funds have no performance rows. “Review this portfolio” combines these details with the exposure breakdown. The listed benchmark identifies the comparison index. The exercise data does not supply a corresponding benchmark return series for these comparisons.

Gemini returns a structured tool decision. It sees the question, schema metadata, and portfolio names. The application runs the query and formats the result locally. Model-generated SQL runs on a query-only SQLite connection with an authorization allowlist, a work budget, and a 100-row output cap. The application keeps the API key in the server environment.

Three read-only analytic views expose portfolio values, sector values, and diversification metrics. They aggregate positions before joining totals, avoiding duplicate counting when comparing a sector with its whole portfolio. These definitions are available to Gemini alongside the original schema.

## Check the numbers

This feature compares recorded weights with weights rebuilt from each supplied quantity × current price. In the Balanced Portfolio, Technology has a recorded weight of 22.00% and a rebuilt weight of 36.22%. Select an allocation to inspect its positions and their percentage-point differences, then download the review note with calculations and source names.

The comparison preserves the original exposure calculator's recorded weights. It shows missing weights explicitly, requires a single currency and complete position inputs, and explains that holding/quote dates are unavailable. Bond values follow the exercise's quantity × price convention. The comparison identifies a reporting difference to investigate.

## Design references

The implementation was written for this exercise. Repository review informed the use of clear calculation definitions, source details, and a question-oriented portfolio layout.

- [Text-to-SQL analytics agent](https://github.com/martinmoll/Text-to-SQL-analytics-agent) describes governed metric definitions and provenance.
- [Portfolio Management Dashboard](https://github.com/GraberTopG/PortfolioManagementDashboard) organizes portfolio inspection around user questions and explains data windows.
- [AI Data Analyst Agent](https://github.com/mariam2004/ai-data-analyst-agent) describes grounded result tables, analysis details, and downloadable reports.

“Check the numbers” combines the supplied exercise's two allocation bases with position evidence and a downloadable review note. This is a custom feature for this solution, with no claim that allocation reconciliation is new to finance.

## Reviewer hosting

Deploy `app.py` from a private GitHub repository to Streamlit Community Cloud with reviewer-only access. Add `GEMINI_API_KEY` at the top level of the app's Secrets settings. Keep `.env` outside the repository. Verify the link using a reviewer account, then try the two performance questions above and click a sector in the Balanced Portfolio allocation chart.

## Validation

On November 6, 2026, the live Gemini-only run with `gemini-3.5-flash-lite` passed all 10 supplied cases. All 15 local regression tests passed. The reported rating → holdings → random holding conversation passed in the browser with zero Gemini calls. A subsequent live question resolved the chosen holding from context and returned its symbol and recorded portfolio percentage using one model call. Browser and Streamlit checks also covered portfolio selection, allocation evidence, comparison calculations, chat history, missing performance records, the 2% unrepresented-weight case, and downloading the review note. Earlier live runs encountered provider 503/504 errors. These results are a snapshot of observed behavior.

The final submission audit also installed the pinned requirements in a fresh isolated environment, extracted the actual submission ZIP, and passed its 15 tests, local CLI queries, SDK imports, and local browser workflow without a saved API key. A separate live CLI run returned the ten active portfolios, and an additional Gemini question correctly found all nine portfolios without performance records. The browser's computed body, heading, and question-input font was verified as Arial.

A subsequent all-portfolio check passed all 13 supplied funds in the UI and local review/conversation path. A live Gemini holdings-count question also passed for each selected fund (13/13), including both bond-only funds. The local suite now contains 16 passing tests, including a regression that checks every fund against its own stored holdings and weights.
