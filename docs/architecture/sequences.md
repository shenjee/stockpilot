# StockPilot Architecture Sequences

This document records the main runtime sequences that the current repository is
designed to support.

## 1. Daily Report Generation

```mermaid
sequenceDiagram
    participant User
    participant CLI as Skill CLI / Entry Script
    participant Orchestrator as ReportOrchestrator
    participant ReportData as ReportDataService
    participant KLineService as KLineDataService
    participant Store as KLineStore
    participant Provider as MarketDataProvider
    participant Indicator as IndicatorService
    participant Rules as RuleEvaluator
    participant Renderer as MarkdownReportRenderer

    User->>CLI: run daily report command
    CLI->>Orchestrator: generate_report(...)
    Orchestrator->>ReportData: build structured report data
    ReportData->>KLineService: get required K-lines
    KLineService->>Store: read local K-lines
    alt local data insufficient
        KLineService->>Provider: fetch remote market data
        Provider-->>KLineService: K-line rows
        KLineService->>Store: upsert local cache
    end
    KLineService-->>ReportData: normalized K-lines
    ReportData->>Indicator: compute indicators
    ReportData->>Rules: evaluate configured rules
    ReportData-->>Orchestrator: report payload
    Orchestrator->>Renderer: render markdown
    Renderer-->>Orchestrator: markdown report
    Orchestrator-->>CLI: save and return result
```

## 2. Chan Analysis In The Debug App

```mermaid
sequenceDiagram
    participant User
    participant App as chan-streamlit app.py
    participant MarketService
    participant AnalysisService
    participant KLineService as KLineDataService
    participant Store as KLineStore
    participant Provider as MarketDataProvider
    participant Chantheory
    participant Charts as figure_builder / renderers

    User->>App: select symbol and request analysis
    App->>MarketService: load K-line data
    MarketService->>KLineService: get_klines(...)
    KLineService->>Store: read local cache
    alt local data insufficient
        KLineService->>Provider: fetch missing data
        Provider-->>KLineService: market rows
        KLineService->>Store: write local cache
    end
    KLineService-->>MarketService: normalized rows
    MarketService-->>App: rows
    App->>AnalysisService: analyze rows
    AnalysisService->>Chantheory: analyze(...)
    Chantheory-->>AnalysisService: stable analysis result
    AnalysisService-->>App: analysis payload
    App->>Charts: build figure from plot primitives
    Charts-->>App: chart figure
    App-->>User: render structure result and chart
```

## 3. Fundamental Screener Refresh

```mermaid
sequenceDiagram
    participant User
    participant App as fundamental-screener app.py
    participant DataService as apps/.../data_service.py
    participant Sync as packages.fundamentalscreener.sync
    participant Sources as data_sources/*
    participant SQLite
    participant Repo as SqliteFundamentalRepository
    participant Core as screening / sector / financial / valuation modules

    User->>App: click refresh / run analysis
    App->>DataService: refresh_market_data(...)
    DataService->>Sync: sync real data to local db
    Sync->>Sources: fetch sectors, bars, constituents, valuations, financials
    Sources-->>Sync: normalized provider rows
    Sync->>SQLite: upsert rows with lineage fields
    Sync-->>DataService: refresh result
    DataService->>Repo: load_snapshot(...)
    Repo->>SQLite: read latest snapshot data
    Repo-->>DataService: MarketSnapshot + quality metadata
    DataService->>Core: compute screening outputs
    Core-->>DataService: sector/company/financial/valuation results
    DataService-->>App: frontend-friendly snapshot result
    App-->>User: render board, warnings, and metadata
```

## 4. Sector-Detail Lazy Loading

```mermaid
sequenceDiagram
    participant User
    participant App as fundamental-screener app.py
    participant DataService as apps/.../data_service.py
    participant Repo as SqliteFundamentalRepository
    participant SQLite
    participant Core as company_ranking / financial_quality / valuation

    User->>App: open one sector detail
    App->>DataService: load sector detail for selected sector
    DataService->>Repo: load snapshot and sector-specific detail inputs
    Repo->>SQLite: read constituents and company-level data
    SQLite-->>Repo: sector detail rows
    Repo-->>DataService: MarketSnapshot subset + warnings
    DataService->>Core: compute company ranking
    DataService->>Core: compute financial comparison
    DataService->>Core: compute valuation comparison
    Core-->>DataService: sector detail outputs
    DataService-->>App: detail payload
    App-->>User: render company, financial, and valuation tables
```

## Notes

- The Streamlit apps are consumers of domain results, not owners of the
  algorithms.
- Local SQLite is the persistence boundary for cached and synchronized runtime
  data.
- External providers are accessed through service or data-source adapters, not
  directly from UI code.
