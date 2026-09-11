# T+0 UI smoke — replay

- git_sha: `761fe5db312765ab302ab9c06637683a0ae0583d`
- status: **pass**
- symbol: sh.600584
- started: 2026-09-11T04:37:49.962Z
- finished: 2026-09-11T04:38:01.985Z
- runtime_dir: `/Users/jishen/Library/Application Support/stockpilot-t0-assistant/stockpilot`
- python: `/Users/jishen/.venvs/czsc/bin/python`

## Steps

- enter_replay_mode: ok · 76ms ([shot](01_replay_setup.png))
- begin_replay: ok · 955ms ([shot](02_replay_started.png))
- step_forward: ok · 1350ms ([shot](03_replay_step_forward.png))
  - 推进 (前进 N 分钟); no obvious hang observed if latency_ms finite
- step_forward_2: ok
- seek_backward: ok · 64ms ([shot](04_replay_seek_back.png))
  - 回退 via progress seek (no dedicated step-back button)
- back_to_live: ok · 1604ms ([shot](05_back_to_live.png))
