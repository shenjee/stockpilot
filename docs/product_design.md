# Stock Pilot Product Design v0.1

## 1. Product Positioning

Stock Pilot is an A-share research and quantitative decision-support system for individual investors who are developing toward a junior fund-manager workflow.

It is not designed for automated trading or black-box stock picking. Its purpose is to help users build an explainable, reviewable, and gradually improving stock analysis process.

Core positioning:

- Observe the market instead of chasing price moves by instinct
- Analyze structure instead of stacking indicators blindly
- Provide quantitative assistance instead of placing automated orders
- Review rules continuously instead of accumulating one-off trading tips
- Generate manual trading prompts that the user executes in a brokerage client

The long-term product form is:

> A research cockpit and manual trading co-pilot for A-share investors.

## 2. Non-Goals

Stock Pilot needs explicit boundaries to avoid uncontrolled scope growth.

Short-term non-goals:

- Automated order placement
- Unsupported next-day limit-up predictions
- Pure LLM-based subjective stock recommendations
- Direct buy/sell calls driven by unverified news
- Complex machine-learning models
- High-frequency trading, tick-level trading, or Level 2 depth strategies

Long-term areas that still require caution:

- Mapping macro events directly to individual-stock buy/sell points
- Generating absolute trading decisions from a single indicator
- Treating online trading tips as permanently valid rules

The system should always output evidence, risks, invalidation conditions, and points that require human confirmation.

## 3. User Profile

The current user can be described as someone who:

- Is growing from a retail investor mindset toward a junior fund-manager workflow
- Already has watchlist, portfolio, and daily market observation needs
- Is learning Chan Theory concepts such as Fractals, Strokes, Segments, Trend Structures, and Pivot Zones
- Wants quantitative signals, but does not want automated trading
- Wants to understand the relationship between macro events, sector rotation, and individual-stock structure
- Wants to turn online tips, trading books, and personal reviews into an evolving strategy library

## 4. Core Workflow

Stock Pilot should be designed around a fund-manager-style daily workflow.

After market close:

1. Read index, watchlist, and portfolio quotes
2. Update the local K-line database
3. Calculate common indicators and price-volume states
4. Analyze portfolio risk and unrealized profit/loss
5. Generate the daily report and key observation items

During review:

1. Inspect portfolio and watchlist price structures
2. Detect structure changes such as Fractals, Strokes, Segments, and Pivot Zones
3. Check which strategy rules were triggered
4. Record whether buy/sell signals were validated by the market
5. Update strategy status or weights

During stock selection:

1. Identify directions from sector rotation and macro events
2. Screen stocks within the relevant industries
3. Check technical structure and quantitative signals
4. Output candidate stocks, entry conditions, invalidation conditions, and risks

During manual trading:

1. The system outputs an action prompt
2. The user executes manually in the brokerage client
3. The system tracks whether the signal is later validated or invalidated

## 5. Capability Layers

Stock Pilot can be divided into eight capability domains.

### 5.1 Data System

Responsible for acquiring, caching, and storing all foundational data.

Includes:

- Index quotes
- Watchlist quotes
- Portfolio quotes
- Historical K-lines
- Volume and turnover amount
- Turnover rate
- PE, PB, and market capitalization
- Sector quotes
- Commodity prices such as gold and crude oil
- FX rates and the US Dollar Index
- Macro and policy events

Phase 1 should prioritize a local SQLite K-line database.

Principles:

- Query local data first
- Fetch external data only when local data is missing
- Write fetched data back to local storage
- Run later analysis against normalized local data

### 5.2 Indicator System

Responsible for common technical indicators and price-volume state calculation.

Recommended Phase 1 coverage:

- MA: 5-day, 10-day, 20-day, and 60-day moving averages
- MACD
- KDJ
- RSI
- BOLL
- Volume moving averages
- Turnover-rate state
- Volume ratio
- Amplitude
- ATR

Indicator output should describe structured states, not only events such as golden crosses or death crosses.

Example:

```text
MACD: above the zero axis, red bars shrinking, upside momentum weakening
RSI: strong but not extreme
BOLL: near upper band, volatility expanding
Volume: above the 20-day average volume
Turnover: high-level divergence
```

### 5.3 Chan Structure System

Responsible for identifying Trend Structure.

Terminology:

- Chan Theory: `Chan Theory`
- Fractal: `Fractal`
- Stroke: `Stroke`
- Segment: `Segment`
- Pivot Zone: `Pivot Zone`
- Trend Structure: `Trend Structure`
- Divergence: `Divergence`
- First / Second / Third Buy Point: `First / Second / Third Buy Point`
- First / Second / Third Sell Point: `First / Second / Third Sell Point`

Suggested iteration order:

1. K-line normalization
2. Inclusion relationship handling
3. Top and bottom fractals
4. Strokes
5. Segments
6. Pivot Zones
7. Divergence
8. Trend Structures
9. Multi-timeframe linkage

The short-term goal is not perfect Chan Theory implementation. The goal is an explainable version.

Version targets for the project-owned adapter output, not for rebuilding a full Chan Theory engine from scratch:

- v0.1: mark top and bottom fractals
- v0.2: connect fractals into strokes
- v0.3: identify segments
- v0.4: identify Pivot Zones
- v0.5: output structure descriptions in text

These targets describe what `chantheory` exposes after mapping `czsc` results into the project schema. They do not imply that Stock Pilot should implement all Fractal, Stroke, Segment, and Pivot Zone recognition rules independently.

### 5.4 Experience Strategy Library

Responsible for collecting experience rules from online sharing, trading books, and personal reviews.

These rules are not absolute truths. They are supporting evidence for decision-making.

Rule sources include:

- Moving-average experience
- Volume experience
- Turnover-rate experience
- Position-sizing experience
- Sector-rotation experience
- Holiday risk experience
- Major-good-news realization experience

Example rules:

```text
Do not buy before the price moves above the 20-day moving average.
Do not sell while the price stays above the 5-day moving average.
Reduce half the position if the price breaks below the 5-day moving average.
Exit if the price breaks below the 10-day moving average.
Do not buy stocks that remain below the 60-day moving average.
In an uptrend, a pullback to the 5-day moving average can be an entry point.
In an uptrend, a pullback to the 20-day moving average may have support.
Low price plus low volume can be watched; low price plus rising volume may be actionable; high price plus rising volume is a warning.
After major positive news is realized, high-open strength should be treated carefully because realization can bring risk.
Reduce exposure before major holidays.
```

The strategy library should support lifecycle states:

- active: currently used
- testing: under observation
- deprecated: retired
- conflict: conflicts with other rules and requires human judgment

Rules can conflict with each other. The system should show the conflict instead of forcing a single conclusion.

Example:

```text
Trend filter: price is still below the 20-day moving average, so buy conditions are not satisfied.
Price-volume signal: low-level volume expansion creates an observation signal.
Combined conclusion: no confirmed entry; add to watchlist and wait for a move above the 20-day line or a valid pullback confirmation.
```

### 5.5 Quantitative Signal System

Responsible for combining indicators, structure, and strategy rules into actionable manual-trading assistance signals.

A signal is not the same as an automated trade.

Suggested signal levels:

```text
Signal Level 0: no signal
Signal Level 1: watch
Signal Level 2: probe
Signal Level 3: confirmed
Signal Level 4: strongly confirmed
Signal Level -1: risk
Signal Level -2: reduce
Signal Level -3: exit
```

Each signal must include:

- Stock
- Timeframe
- Signal level
- Triggered rules
- Supporting evidence
- Suppressing factors
- Invalidation conditions
- Risk notes
- Suggested action
- Whether human confirmation is required

Example:

```text
Stock: 600111 Northern Rare Earth
Timeframe: daily + 30-minute
Signal: watch entry
Evidence:
- Daily pullback held above the lower edge of the central zone
- A 30-minute bottom fractal appeared
- MACD green bars are shrinking
- Volume contracted
- Turnover rate declined
Invalidation:
- Breaks below the previous low
- Breaks below the lower edge of the central zone
Suggested action:
- Add to watchlist
- Do not take a heavy position directly
```

### 5.6 Sector Rotation System

Responsible for identifying where market capital is moving.

Sector and leader effects are strong in the A-share market, so individual-stock signals should be filtered by sector context.

Key observations:

- Sector price change
- Sector turnover amount
- Sector volume expansion
- Leading-stock performance
- Number of rising stocks within the sector
- Sector-rotation persistence
- Relative strength versus major indices

Principles:

- Downgrade individual technical entries if there is no sector support
- If the sector is strong but the individual structure is weak, only mark as watch
- Macro events should first adjust sector observation weights, not directly generate individual-stock entries

### 5.7 Macro Event System

Responsible for recording international relations, policy changes, commodity prices, and industry impacts.

Event types of interest include:

- Changes in relations among Iran, the United States, Russia, China, and related countries
- The Strait of Hormuz and broader Middle East developments
- Gold, crude oil, and US Dollar Index changes
- US-China trade negotiations
- Easing or tightening of AI chip controls
- Agricultural trade changes
- Aviation procurement, Boeing engines, and related industrial events

Macro events should be used through this chain:

```text
Event -> affected themes -> mapped industries -> observation direction -> sector and stock-market validation
```

Example:

```text
Middle East tension easing:
- Crude-oil risk premium declines
- Oil and gas become bearish-watch sectors
- Gold safe-haven demand may weaken
- Some downstream chemical companies may benefit from lower input costs
```

```text
AI chip restrictions easing:
- AI computing becomes bullish
- Semiconductor equipment becomes bullish
- Advanced packaging becomes bullish
- AI software becomes bullish
- Domestic substitution may diverge and needs market validation
```

Macro events should not directly generate trading instructions. Stock recommendations should only appear when macro direction, sector rotation, individual-stock structure, and quantitative signals confirm together.

### 5.8 Portfolio and Review System

Responsible for portfolio monitoring, risk exposure, and strategy validation.

Includes:

- Portfolio market value
- Unrealized profit/loss
- Single-stock position size
- Sector concentration
- Risk level
- Stop conditions
- Strategy hit records
- Post-trade review

The portfolio system should answer:

- Where are the current portfolio risks?
- Which holdings triggered reduce or exit signals?
- Which holdings remain in valid trends?
- Is the original entry reason still valid?
- Should the original strategy be downgraded or retired?

## 6. Product Roadmap

### 6.1 Phase-to-PRD Module Mapping

Product phases define delivery order. Section 5 defines long-term module boundaries. They are not strictly one-to-one: a module may first ship basic capabilities and later gain signal synthesis, risk constraints, and portfolio assistance.

| Phase | Phase Name | Primary PRD Modules | Notes |
|---|---|---|---|
| Phase 0 | Product Design and Task Breakdown | Overall product boundaries and task breakdown | Define positioning, non-goals, module boundaries, and the first executable tasks |
| Phase 1 | Basic Research Desk | 5.1 Data System, 5.2 Indicator System, 5.8 Portfolio and Review System (basic part) | First complete the local K-line database, daily reports, common indicators, price-volume states, and basic portfolio risk |
| Phase 2 | Structure Analysis Engine | 5.3 Chan Structure System | Start with explainable Fractals, Strokes, Segments, Pivot Zones, and early Divergence detection; do not try to implement perfect Chan Theory in one step |
| Phase 3 | Strategy Rules and Quantitative Signals | 5.4 Experience Strategy Library, 5.5 Quantitative Signal System, 5.8 Portfolio and Review System (signal part) | Combine indicators, structure, rules, and position risk into reviewable watch, reduce, exit, and related signals |
| Phase 4 | Sector Rotation | 5.6 Sector Rotation System | Identify where market capital is moving and use sector support to confirm or downgrade individual-stock signals |
| Phase 5 | Macro Events and Industry Mapping | 5.7 Macro Event System | Record events, map possible industry impact, and wait for sector and individual-stock market validation |
| Phase 6 | Stock Recommendation and Portfolio Assistance | Combined application of 5.5 Quantitative Signal System, 5.6 Sector Rotation System, 5.7 Macro Event System, and 5.8 Portfolio and Review System | Only output candidate stocks and portfolio assistance when macro direction, sector rotation, individual structure, quantitative signals, and risk conditions confirm together |

### Phase 0: Product Design and Task Breakdown

Goals:

- Define product positioning
- Define non-goals
- Define module boundaries
- Define the signal system
- Define the strategy-library format
- Break down the first executable tasks

Deliverables:

- `docs/product_design.md`
- Initial task backlog

### Phase 1: Basic Research Desk

Corresponding PRD modules: 5.1 Data System, 5.2 Indicator System, and 5.8 Portfolio and Review System (basic part).

Goal:

Answer reliably every day:

> What happened today to the stocks I care about?

Scope:

- Local K-line database
- Watchlist and portfolio daily reports
- Common indicators
- Volume and turnover-rate states
- Basic portfolio profit/loss and risk

Non-goals:

- Complex Chan Theory
- Macro-event-driven stock recommendations
- Automated stock picking

### Phase 2: Structure Analysis Engine

Corresponding PRD module: 5.3 Chan Structure System.

Goal:

Answer:

> What trend structure is this stock currently in?

Scope:

- Fractals
- Strokes
- Segments
- Pivot Zones
- Early Divergence detection
- Multi-timeframe observation

Output:

- Visual structure output as the primary output
- Structure-change alerts
- Short text summaries as supporting output
- Structure-only candidate buy/sell points

Implementation boundary:

- Phase 2 does not keep piling Chan logic into the daily-report script
- Phase 2 does not rebuild a full Chan core from scratch
- Phase 2 uses open-source `czsc` as the default underlying Chan analysis engine
- The project keeps its own `chantheory` adapter layer for input normalization, parameter constraints, unified schema, plotting data, and summaries
- Skill scripts call the `chantheory` adapter layer and turn the results into Markdown, text summaries, or signal inputs
- The UI layer focuses on visualization and should not own the core Chan calculations
- Visual output should take priority over natural-language description, with language kept only as a supporting layer
- Candidate buy/sell points in Phase 2 are structure candidates only; they must not become standalone trading instructions before Phase 3 signal synthesis

Recommended architecture:

```text
Local K-line database / market data
        ->
chantheory adapter
  ├─ normalize project K-line data
  ├─ call czsc
  └─ map results to project schema / plot_primitives / summary / warnings
        ->
  ├─ skill scripts: text reports, structure summaries, agent use
  ├─ Streamlit app: algorithm verification, structure inspection, parameter debugging
  └─ desktop / local app: K-line overlays, fractal/stroke/segment/central-zone visualization, interactive review
```

Reasons:

- `czsc` already provides mature core Chan capabilities and is a strong default engine for Phase 2
- Reimplementing Fractals, Strokes, Segments, and Pivot Zones from scratch would duplicate effort and increase testing cost
- Keeping a project-owned adapter layer avoids binding skills, agents, and UIs directly to `czsc` native objects and version details
- Once the result model is unified, skills, agents, apps, and debug tools can all consume the same contract

Recommended delivery order:

1. Verify that `czsc` matches the current A-share K-line format and timeframe requirements
2. Build the `chantheory` adapter layer and unify inputs, outputs, and `plot_primitives`
3. Use Streamlit to build a debug and verification view for checking whether Fractals, Strokes, Segments, and Pivot Zones are drawn correctly
4. Let the skill consume the adapter layer and output short text summaries plus structured results
5. Build a local UI or desktop app only after the structure rules and output format stabilize

Suggested inputs for the `chantheory` adapter layer:

- Standardized OHLCV K-line sequences
- Timeframe parameters
- `czsc` analysis parameters
- Adjustment mode: forward-adjusted, backward-adjusted, or unadjusted
- Trading-calendar assumptions and suspension / missing-bar handling
- Minimum bar-count requirements for each structure level
- Intraday aggregation rules for minute-level K-lines
- Optional manual correction rules

Suggested outputs for the `chantheory` adapter layer:

- `symbol`
- `timeframe`
- `source`
- `engine`
- `engine_version`
- `parameters`
- `fractals`
- `strokes`
- `segments`
- `pivot_zones`
- `divergences`
- `structure_alerts`
- `candidate_buy_points`
- `candidate_sell_points`
- `plot_primitives`
- `summary`
- `warnings`
- `meta`

Notes:

- The UI requirement in Phase 2 is real because Chan structures are much easier for users to understand visually than through plain text alone
- For Chan Theory, visual output should be the primary output, while text should be limited to summaries, captions, and agent-readable explanations
- Streamlit is a strong fit for Phase 2 debugging and validation because it allows fast visual inspection of structure recognition results
- But the UI should not rely on openclaw, codex, or similar agent runtimes as a long-term interaction host
- A better approach is to build the UI as a local desktop app or local web app that reads Chan results through a unified analysis interface
- `czsc` owns the underlying Chan core recognition, while `chantheory` owns project-side adaptation, constraints, and unified output
- The skill and the desktop app should both depend on the `chantheory` adapter layer instead of UI components or `czsc` internals

### Phase 3: Strategy Rules and Quantitative Signals

Corresponding PRD modules: 5.4 Experience Strategy Library, 5.5 Quantitative Signal System, and 5.8 Portfolio and Review System (signal part).

Goal:

Answer:

> Which rules were triggered, and did they form an actionable observation or operation signal?

Scope:

- Strategy rule library
- Moving-average rules
- Price-volume rules
- Position rules
- Holiday rules
- Strategy conflict display
- Signal-level synthesis

Output:

- Watch signals
- Probe signals
- Confirmed signals
- Reduce signals
- Exit signals

### Phase 4: Sector Rotation

Corresponding PRD module: 5.6 Sector Rotation System.

Goal:

Answer:

> Where is market capital moving, and does an individual-stock signal have sector support?

Scope:

- Sector price changes
- Sector turnover amount
- Leading stocks
- Sector relative strength
- Sector persistence
- Individual-signal sector filtering

### Phase 5: Macro Events and Industry Mapping

Corresponding PRD module: 5.7 Macro Event System.

Goal:

Answer:

> Which industries may be affected by macro events, and is the market validating that impact?

Scope:

- International-relations events
- Commodity prices
- US Dollar Index
- Trade policy
- Chip controls
- Industry mapping for agriculture, aviation, energy, gold, and related sectors

Output:

- Event observations
- Industry impact directions
- Sector validation status
- Candidate industry pool

### Phase 6: Stock Recommendation and Portfolio Assistance

Corresponding PRD modules: combined application of 5.5 Quantitative Signal System, 5.6 Sector Rotation System, 5.7 Macro Event System, and 5.8 Portfolio and Review System.

Goal:

Answer:

> Under the current market environment and industry direction, which stocks are worth watching or acting on?

Recommendation requirements:

- Macro or event direction supports the theme
- Sector rotation confirms the theme
- Individual technical structure is healthy
- Quantitative signal is triggered
- Risk can be defined
- Invalidation condition is clear

Output:

- Candidate stocks
- Entry conditions
- Exit conditions
- Position-size range
- Risk level
- Human confirmation items

## 7. Skill Organization

In the short term, continue using one main skill:

```text
china-stock-analysis
```

Reasons:

- Requirements are still evolving
- A single skill is easier to iterate
- The current skill already has report and configuration foundations
- Splitting too early increases maintenance complexity
- `czsc` is a good underlying engine, but it should not become a naked dependency of the upper-layer skill

Additional guidance:

- Keep `china-stock-analysis` as the main skill entry in the short term
- Let new Phase 2 Chan capability land first as a project-owned `chantheory` adapter layer consumed by the main skill
- Let `chantheory` integrate `czsc` by default while exposing a stable project schema and plotting data to the outside
- Decide whether Chan should become a separate skill only after the analysis interfaces and parameter boundaries stabilize
- Do not place the Chan UI directly inside the skill; UI fits better as a separate local application
- `chantheory` should be treated as an independent integration layer, with skills, agents, apps, and debug tools all acting as consumers

In the medium to long term, the system can be split into multiple skills:

```text
stock-data-engine
stock-technical-engine
stock-strategy-engine
stock-sector-engine
stock-macro-event-engine
stock-portfolio-pilot
```

Split when:

- Module boundaries stabilize
- Data interfaces stabilize
- Code size grows significantly
- Other agents need to reuse only part of the capability

## 8. Suggested Directory Structure

This repository is the StockPilot product codebase. Repository-level design documents stay under `docs/`, installable skills stay under `skills/<skill-name>/`, project-owned adapters stay under `packages/`, visualization apps stay under `apps/`, and reusable CLI/core capabilities should remain shared across these consumers:

```text
stockpilot/
├── README.md
├── docs/
│   ├── product_design.md
│   ├── product_design.zh.md
│   ├── phase2_tasks.md
│   └── chan_theory_v0.1.md
├── packages/
│   └── chantheory/
│       ├── __init__.py
│       ├── normalize.py
│       ├── adapters.py
│       ├── schema.py
│       ├── describe.py
│       ├── plotting.py
│       └── config.py
├── apps/
│   └── chan-streamlit/
│       ├── README.md
│       ├── app.py
│       └── pages/
└── skills/
    └── china-stock-analysis/
        ├── SKILL.md
        ├── scripts/
        │   ├── generate_report.py
        │   ├── local_db.py
        │   ├── market_data.py
        │   ├── indicators.py
        │   ├── strategy_engine.py
        │   ├── sector_rotation.py
        │   ├── macro_events.py
        │   └── portfolio_risk.py
        ├── assets/
        │   ├── config_templates/
        │   └── strategies/
        └── references/
```

This is a target structure, not a one-shot implementation plan.

## 9. First Task Batch

P0:

- Build a local SQLite K-line database
- Design a unified K-line data structure
- Persist fetched historical K-lines into the local database
- Add MA, MACD, RSI, KDJ, and BOLL calculations
- Add volume and turnover-rate state descriptions

P1:

- Create the strategy-rule Markdown directory
- Add the first batch of moving-average, price-volume, and position experience rules
- Prepare Phase 2 by validating whether `czsc` matches the current A-share K-line input format, adjustment mode, and timeframe assumptions
- Integrate indicator states into the daily report

P2:

- Build the `chantheory` normalization and `czsc` adapter layer
- Define the unified structure output schema
- Output `plot_primitives` for chart rendering
- Build a Streamlit debug view to validate `czsc` structure rendering
- Build the signal-level synthesis model
- Add portfolio reduce and exit signals

P3:

- Integrate sector-rotation data
- Add sector strength analysis
- Build a manual macro-event-to-industry mapping table
- Require stock recommendations to pass both sector and structure filters

## 10. Design Principles

Future Stock Pilot iterations should follow these principles:

- Data before analysis
- Observation before signals
- Structure before recommendations
- Human confirmation before automation
- Every conclusion must have evidence
- Every signal must have invalidation conditions
- Every rule must be reviewable, down-weightable, and removable
- Macro events must wait for sector and individual-stock market validation
- No strong buy/sell decision should come from a single indicator

## 11. Minimum Viable Product

The Stock Pilot MVP does not need to recommend stocks.

The first usable version should be able to:

- Update watchlist and portfolio data daily
- Save historical K-lines into a local database
- Output basic technical indicator states
- Output volume and turnover-rate states
- Warn about portfolio risks
- Record and display experience strategy rules
- Generate stable Markdown daily reports

After the MVP is stable, the system can move into structure analysis, signal synthesis, sector rotation, and macro event support.
