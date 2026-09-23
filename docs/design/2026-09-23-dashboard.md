# WindOps workspace redesign

User wants both a modern forecasting workspace and richer interaction. The page is for a wind operator comparing two turbines over the next 24/48 hours. Primary task: inspect a dated forecast, run the agent and export its numerical results.

Direction: a compact weather operations desk. Cool white surfaces (#ffffff), blue-grey canvas (#f3f6fb), navy type (#172b45), blue T1 (#235be8), amber T2 (#b56b16), teal status (#167465). Display uses Trebuchet MS with system fallback; body uses system-ui; numeric data uses SFMono/Consolas. No remote font dependencies.

Layout: compact brand header with the archive period; full-width forecast workspace without a sidebar or section-jump menu. Three forecast summaries sit above a large two-series chart beside the live agent stages. Data provenance and exports remain accessible below. On small screens the agent follows the chart.

Signature: a two-turbine trajectory with a synchronized hourly inspector. This is real forecast data, not decorative wind art. The 24/48 hour toggle, date arrows and comparison mode alter actual data views. Downloads remain explicitly full 48 hours for both turbines.

The site has one calculation action: the numerical model forecasts power and OpenAI orchestrates the tools and explains the result. Without an API key, the run action is disabled with an explanation; archive viewing and exports remain available.

Quiet surrounding chrome; no decorative KPI metrics, pretend notifications or fabricated agent activity. Preserve all current model, time, source verification and error handling. The prepared model remains unchanged.

Forecast events sit below the hourly inspector and select real hours. The grounded question form is a separate prominent card with a blue submit button: above the agent stages beside the chart on desktop, directly after the forecast controls and before the summary/chart on narrow screens. The answer stays in the same card. A separate analytics panel uses functional tabs for consecutive-issue comparison, January actual-versus-predicted validation, and persistent run history. Each chart has a values table. Comparison joins identical target hours; January scores are explicitly descriptive. Saved runs show their original forecast/model/explanation with a snapshot banner and snapshot CSV, never a silently recomputed result. Questions on a saved run use that same snapshot.

Verification: browser desktop and mobile, both turbine modes and horizons, date boundary arrows, event selection, keyboard-accessible tabs and tables, comparison filters, January day/horizon filters, saved-run reopening and export, real grounded question, restart recovery, console errors, and relevant backend tests.

Chart treatment follows [diagram-design](https://github.com/cathrynlavery/diagram-design), adapted to the WindOps palette and local fonts: larger axis labels, explicit units and timezone, a stronger zero baseline, restrained gridlines, and legends below a hairline. All 24/48 hourly values and straight segments stay intact; no smoothing or downsampling. Only selected-hour and comparison-highlight dots remain (isolated observations remain visible). Power-event bands have labelled turbine lanes; the wind plot omits those power bands. The selected hour is marked with a faint interval band.
