# T+0 UI smoke — replay

- git_sha: `e28737965a6362cfb09885ae32c26dd68faba14a`
- status: **pass**
- symbol: sh.600584
- started: 2026-09-11T16:49:30.087Z
- finished: 2026-09-11T16:49:41.736Z
- runtime_dir: `/Users/jishen/Library/Application Support/stockpilot-t0-assistant/stockpilot`
- python: `/Users/jishen/.venvs/czsc/bin/python`

## Steps

- enter_replay_mode: ok · 87ms ([shot](01_replay_setup.png))
- begin_replay: ok · 700ms ([shot](02_replay_started.png))
- engine_version: ok
- step_forward: ok · 818ms ([shot](03_replay_step_forward.png))
  - 推进 (前进 N 分钟); no obvious hang observed if latency_ms finite
- step_forward_2: ok
- seek_backward: ok · 71ms ([shot](04_replay_seek_back.png))
  - 回退 via progress seek (no dedicated step-back button)
- back_to_live: ok · 1599ms ([shot](05_back_to_live.png))
