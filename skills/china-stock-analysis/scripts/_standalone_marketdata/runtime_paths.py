import json
import os
from pathlib import Path


LOCAL_CONFIG_NAMES = (
    "china-stock-analysis.local.json",
    "china-stock-analysis.json",
    "china-stock-daily-tracker.local.json",
    "china-stock-daily-tracker.json",
)


class RuntimePaths:
    """运行期路径：skill目录和私有工作区分离。"""

    def __init__(self, config_file: str | None = None):
        config = self._load_runtime_config(config_file)
        root_value = (
            os.environ.get("CHINA_STOCK_ANALYSIS_WORKSPACE")
            or os.environ.get("CHINA_STOCK_DAILY_TRACKER_WORKSPACE")
            or config.get("workspace")
        )
        root = Path(root_value).expanduser().resolve() if root_value else Path.cwd().resolve()
        runtime_value = config.get("runtime_dir", "stockpilot")

        self.workspace = root
        self.runtime_dir = self._resolve_workspace_path(runtime_value)
        self.config_dir = self._resolve_runtime_path(config.get("config_dir", "config"))
        self.report_dir = self._resolve_runtime_path(config.get("reports_dir", "reports"))
        self.db_dir = self._resolve_runtime_path(config.get("db_dir", "db"))
        self.strategy_dir = self._resolve_runtime_path(config.get("strategies_dir", "strategies"))
        data_source = config.get("data_source", {}) if isinstance(config.get("data_source"), dict) else {}
        self.market_data_provider = config.get("market_data_provider") or data_source.get("provider", "tencent")

    def _load_runtime_config(self, config_file: str | None = None) -> dict:
        path = (
            config_file
            or os.environ.get("CHINA_STOCK_ANALYSIS_CONFIG")
            or os.environ.get("CHINA_STOCK_DAILY_TRACKER_CONFIG")
        )
        if path:
            return self._read_json_config(Path(path).expanduser())

        for name in LOCAL_CONFIG_NAMES:
            candidate = Path.cwd() / name
            if candidate.exists():
                return self._read_json_config(candidate)
        return {}

    def _read_json_config(self, path: Path) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            print(f"[WARN] 加载运行配置失败 {path}: {exc}")
            return {}

    def _resolve_workspace_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (self.workspace / path).resolve()

    def _resolve_runtime_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (self.runtime_dir / path).resolve()

    def ensure_dirs(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.db_dir.mkdir(parents=True, exist_ok=True)
