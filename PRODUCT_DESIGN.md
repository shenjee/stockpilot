# china-stock-daily-tracker Product Design v0.1

## 1. Product Positioning

china-stock-daily-tracker is an A-share research and quantitative decision-support system for individual investors who are developing toward a junior fund-manager workflow.

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

china-stock-daily-tracker needs explicit boundaries to avoid uncontrolled scope growth.

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
- Is learning Chan theory concepts such as fractals, strokes, segments, trend types, and central zones
- Wants quantitative signals, but does not want automated trading
- Wants to understand the relationship between macro events, sector rotation, and individual-stock structure
- Wants to turn online tips, trading books, and personal reviews into an evolving strategy library

## 4. Core Workflow

china-stock-daily-tracker should be designed around a fund-manager-style daily workflow.

After market close:

1. Read index, watchlist, and portfolio quotes
2. Update the local K-line database
3. Calculate common indicators and price-volume states
4. Analyze portfolio risk and unrealized profit/loss
5. Generate the daily report and key observation items

During review:

1. Inspect portfolio and watchlist price structures
2. Detect structure changes such as fractals, strokes, segments, and central zones
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

china-stock-daily-tracker can be divided into eight capability domains.

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

Responsible for identifying trend structure.

Suggested iteration order:

1. K-line normalization
2. Inclusion relationship handling
3. Top and bottom fractals
4. Strokes
5. Segments
6. Central zones
7. Divergence
8. Trend types
9. Multi-timeframe linkage

The short-term goal is not perfect Chan theory implementation. The goal is an explainable version.

Version targets:

- v0.1: mark top and bottom fractals
- v0.2: connect fractals into strokes
- v0.3: identify segments
- v0.4: identify central zones
- v0.5: output structure descriptions in text

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

### Phase 0: Product Design and Task Breakdown

Goals:

- Define product positioning
- Define non-goals
- Define module boundaries
- Define the signal system
- Define the strategy-library format
- Break down the first executable tasks

Deliverables:

- `PRODUCT_DESIGN.md`
- Initial task backlog

### Phase 1: Basic Research Desk

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

- Complex Chan theory
- Macro-event-driven stock recommendations
- Automated stock picking

### Phase 2: Structure Analysis Engine

Goal:

Answer:

> What trend structure is this stock currently in?

Scope:

- Fractals
- Strokes
- Segments
- Central zones
- Early divergence detection
- Multi-timeframe observation

Output:

- Structure descriptions
- Structure-change alerts
- Potential buy/sell point candidates

### Phase 3: Strategy Rules and Quantitative Signals

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
china-stock-daily-tracker
```

Reasons:

- Requirements are still evolving
- A single skill is easier to iterate
- The current skill already has report and configuration foundations
- Splitting too early increases maintenance complexity

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

In the short term, organize modules inside the current skill:

```text
china-stock-daily-tracker/
├── SKILL.md
├── PRODUCT_DESIGN.md
├── PRODUCT_DESIGN.zh.md
├── scripts/
│   ├── generate_report.py
│   ├── local_db.py
│   ├── fetch_kline.py
│   ├── indicators.py
│   ├── chan_structure.py
│   ├── strategy_engine.py
│   ├── sector_rotation.py
│   ├── macro_events.py
│   └── portfolio_risk.py
├── assets/
│   ├── config/
│   │   ├── watchlist.yaml
│   │   ├── portfolio.yaml
│   │   └── index_pool.yaml
│   ├── db/
│   │   └── stocks.sqlite
│   ├── strategies/
│   │   ├── ma_rules.md
│   │   ├── volume_price_rules.md
│   │   ├── position_rules.md
│   │   ├── sector_rules.md
│   │   └── event_rules.md
│   └── reports/
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
- Implement fractal detection
- Implement stroke detection
- Integrate indicator states into the daily report

P2:

- Implement segment detection
- Implement central-zone detection
- Build the signal-level synthesis model
- Add portfolio reduce and exit signals

P3:

- Integrate sector-rotation data
- Add sector strength analysis
- Build a manual macro-event-to-industry mapping table
- Require stock recommendations to pass both sector and structure filters

## 10. Design Principles

Future china-stock-daily-tracker iterations should follow these principles:

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

The china-stock-daily-tracker MVP does not need to recommend stocks.

The first usable version should be able to:

- Update watchlist and portfolio data daily
- Save historical K-lines into a local database
- Output basic technical indicator states
- Output volume and turnover-rate states
- Warn about portfolio risks
- Record and display experience strategy rules
- Generate stable Markdown daily reports

After the MVP is stable, the system can move into structure analysis, signal synthesis, sector rotation, and macro event support.
