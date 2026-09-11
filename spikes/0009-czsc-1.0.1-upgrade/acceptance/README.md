# #176 acceptance harness

Scripts and artifacts for czsc 1.0.1 regression / performance / downstream smoke.

## Gate

While #174/#175 are not accepted: do **not** close #176, do **not** update formal
fixtures, do **not** start #177. Record git SHA on every artifact; re-run after
PR #179 review changes. Live / Replay / chan-viewer UI smoke evidence is under
`artifacts/ui-smoke/`; confirmed behavior/perf items no longer block closure.

## Commands

```bash
source ~/.venvs/czsc/bin/activate
cd /path/to/stockpilot

python spikes/0009-czsc-1.0.1-upgrade/acceptance/compare_baseline.py
python spikes/0009-czsc-1.0.1-upgrade/acceptance/probe_first_bar_status.py
python spikes/0009-czsc-1.0.1-upgrade/acceptance/incremental_consistency.py
python spikes/0009-czsc-1.0.1-upgrade/acceptance/benchmark_compare.py
python spikes/0009-czsc-1.0.1-upgrade/acceptance/benchmark_compare.py --multi-round \
  --scenario 5m_real_600584_548 --scenario daily_synthetic_120
python spikes/0009-czsc-1.0.1-upgrade/acceptance/profile_signal_replay.py
python spikes/0009-czsc-1.0.1-upgrade/acceptance/downstream_smoke.py
```

chan-viewer UI smoke（需本机已 `streamlit run apps/chan-viewer/app.py --server.port 8501`）：

```bash
python spikes/0009-czsc-1.0.1-upgrade/acceptance/ui_smoke_chan_viewer.py
```

T+0 Assistant Live/Replay UI smoke（真实 Python backend，不下单）：

```bash
source ~/.venvs/czsc/bin/activate
cd apps/t0-assistant
npm run build
T0_PYTHON="$HOME/.venvs/czsc/bin/python" \
./node_modules/.bin/electron ../../spikes/0009-czsc-1.0.1-upgrade/acceptance/ui_smoke_t0_electron.mjs
```

Artifacts: `artifacts/ui-smoke/{live,replay,chan-viewer}/`.

Old-engine performance side requires:

- worktree: `../stockpilot-wt-01012` (main / czsc 0.10.12 pin)
- venv: `~/.venvs/czsc01012-bench`

## Report

See `../acceptance-report-176.md`.
