"""国泰海通 QMT / xtquant 实时行情探测 demo.

这个文件刻意保持独立，不接入现有选股链路。用途是先在安装了 QMT MiniQMT
和 xtquant 的机器上确认：普通实时行情、1 分钟线、分笔/逐笔成交、逐笔委托、
委托队列这些能力是否可用。
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import io
import json
import os
import platform
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Callable


DEFAULT_SYMBOLS = ["600000.SH", "000001.SZ"]

MARKET_DATA_PERIODS: tuple[tuple[str, str], ...] = (
    ("minute_1m", "1m"),
    ("tick", "tick"),
    ("l2_transaction", "l2transaction"),
    ("l2_order", "l2order"),
    ("l2_order_queue", "l2orderqueue"),
)


def import_xtdata() -> tuple[Any | None, str | None, str]:
    """Import xtquant.xtdata lazily so local tests do not require QMT."""

    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            module = importlib.import_module("xtquant.xtdata")
        return module, None, (stdout.getvalue() + stderr.getvalue()).strip()
    except Exception as exc:  # pragma: no cover - depends on local QMT env
        return None, f"{type(exc).__name__}: {exc}", (stdout.getvalue() + stderr.getvalue()).strip()


def inspect_environment() -> dict[str, Any]:
    """Collect import diagnostics without importing xtquant itself."""

    info: dict[str, Any] = {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "xtquant_spec_found": False,
        "hints": [],
    }

    try:
        info["xtquant_distribution_version"] = importlib.metadata.version("xtquant")
    except Exception:
        info["xtquant_distribution_version"] = None

    spec = importlib.util.find_spec("xtquant")
    if spec is None:
        info["hints"].append("当前 Python 环境没有找到 xtquant 包；请在安装 QMT/xtquant 的 Python 中运行。")
        return info

    info["xtquant_spec_found"] = True
    info["xtquant_origin"] = spec.origin
    package_dirs = list(spec.submodule_search_locations or [])
    info["xtquant_package_dirs"] = package_dirs

    native_extensions: list[str] = []
    for package_dir in package_dirs:
        try:
            for filename in sorted(os.listdir(package_dir)):
                if filename.endswith((".pyd", ".so", ".dll")):
                    native_extensions.append(filename)
        except OSError as exc:
            info["hints"].append(f"无法读取 xtquant 包目录 {package_dir}: {exc}")

    info["native_extensions"] = native_extensions[:40]

    has_windows_binary = any("win_amd64" in filename or filename.endswith(".dll") for filename in native_extensions)
    has_linux_binary = any(filename.endswith(".so") or "linux" in filename.lower() for filename in native_extensions)
    if platform.system().lower() != "windows" and has_windows_binary and not has_linux_binary:
        info["hints"].append(
            "当前 xtquant 包包含 Windows 原生扩展/DLL，但正在非 Windows Python 中运行；"
            "QMT/xtquant 通常需要在 Windows 的 MiniQMT 同机 Python 环境运行，WSL/Linux 下会导入 datacenter 失败。"
        )

    return info


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def preview_data(data: Any, max_items: int = 3) -> dict[str, Any]:
    """Return a compact JSON-safe preview for xtdata's varied return shapes."""

    if hasattr(data, "head") and hasattr(data, "to_dict"):
        try:
            head = data.head(max_items)
            return {
                "type": type(data).__name__,
                "rows": head.to_dict("records"),
                "columns": [str(column) for column in getattr(data, "columns", [])],
            }
        except Exception:
            pass

    if isinstance(data, Mapping):
        sample: dict[str, Any] = {}
        for key, value in list(data.items())[:max_items]:
            nested = preview_data(value, max_items=max_items)
            sample[str(key)] = nested.get("sample", nested)
        return {"type": "dict", "size": len(data), "sample": sample}

    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        sample = []
        for item in list(data)[:max_items]:
            nested = preview_data(item, max_items=max_items)
            sample.append(nested.get("sample", nested))
        return {"type": type(data).__name__, "size": len(data), "sample": sample}

    return {"type": type(data).__name__, "sample": _safe_scalar(data)}


def _success(name: str, data: Any) -> dict[str, Any]:
    return {"name": name, "ok": True, "preview": preview_data(data)}


def _failure(name: str, exc: Exception | str) -> dict[str, Any]:
    message = str(exc) if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    return {"name": name, "ok": False, "error": message}


def _run_check(checks: list[dict[str, Any]], name: str, func: Callable[[], Any]) -> None:
    try:
        checks.append(_success(name, func()))
    except Exception as exc:
        checks.append(_failure(name, exc))


def _get_full_tick(xtdata: Any, symbols: list[str]) -> Any:
    func = getattr(xtdata, "get_full_tick", None)
    if not callable(func):
        raise RuntimeError("xtdata.get_full_tick 不存在，当前 xtquant 版本可能不支持普通实时快照")
    return func(symbols)


def _get_market_data_ex(xtdata: Any, symbols: list[str], period: str, count: int = 5) -> Any:
    func = getattr(xtdata, "get_market_data_ex", None)
    if not callable(func):
        raise RuntimeError("xtdata.get_market_data_ex 不存在，无法探测该周期行情")

    kwargs = {
        "field_list": [],
        "stock_list": symbols,
        "period": period,
        "start_time": "",
        "end_time": "",
        "count": count,
        "dividend_type": "none",
        "fill_data": False,
    }
    try:
        return func(**kwargs)
    except TypeError:
        return func(
            kwargs["field_list"],
            kwargs["stock_list"],
            kwargs["period"],
            kwargs["start_time"],
            kwargs["end_time"],
            kwargs["count"],
            kwargs["dividend_type"],
            kwargs["fill_data"],
        )


def _subscribe_period(xtdata: Any, symbols: list[str], period: str, seconds: int) -> dict[str, Any]:
    subscribe_quote = getattr(xtdata, "subscribe_quote", None)
    if not callable(subscribe_quote):
        raise RuntimeError("xtdata.subscribe_quote 不存在，无法探测订阅推送")

    events: list[dict[str, Any]] = []
    sequence_ids: list[Any] = []

    def callback(payload: Any) -> None:
        events.append({"period": period, "preview": preview_data(payload)})

    for symbol in symbols:
        sequence_ids.append(
            subscribe_quote(symbol, period=period, count=0, callback=callback)
        )

    if seconds > 0:
        time.sleep(seconds)

    unsubscribe_quote = getattr(xtdata, "unsubscribe_quote", None)
    if callable(unsubscribe_quote):
        for sequence_id in sequence_ids:
            try:
                unsubscribe_quote(sequence_id)
            except Exception as exc:
                events.append({"unsubscribe_error": f"{type(exc).__name__}: {exc}"})

    return {
        "period": period,
        "sequence_ids": sequence_ids,
        "seconds": seconds,
        "event_count": len(events),
        "events": events[:5],
    }


def run_probe(
    symbols: Sequence[str] | None = None,
    *,
    xtdata: Any | None = None,
    seconds: int = 10,
    subscribe: bool = False,
) -> dict[str, Any]:
    """Probe QMT quote capabilities and return a JSON-serializable report."""

    normalized_symbols = list(symbols or DEFAULT_SYMBOLS)
    checks: list[dict[str, Any]] = []
    environment = inspect_environment()

    if xtdata is None:
        xtdata, import_error, import_output = import_xtdata()
        if xtdata is None:
            check = _failure("import_xtquant", import_error or "xtquant.xtdata 导入失败")
            if import_output:
                check["import_output"] = import_output
            checks.append(check)
            return {
                "xtquant_installed": False,
                "symbols": normalized_symbols,
                "seconds": seconds,
                "subscribe": subscribe,
                "environment": environment,
                "checks": checks,
            }

    import_check = _success("import_xtquant", getattr(xtdata, "__name__", type(xtdata).__name__))
    if "import_output" in locals() and import_output:
        import_check["import_output"] = import_output
    checks.append(import_check)
    _run_check(checks, "full_tick_realtime_snapshot", lambda: _get_full_tick(xtdata, normalized_symbols))

    for check_name, period in MARKET_DATA_PERIODS:
        _run_check(
            checks,
            f"market_data_ex_{check_name}",
            lambda period=period: _get_market_data_ex(xtdata, normalized_symbols, period),
        )

    if subscribe:
        for check_name, period in MARKET_DATA_PERIODS:
            _run_check(
                checks,
                f"subscribe_quote_{check_name}",
                lambda period=period: _subscribe_period(xtdata, normalized_symbols, period, seconds),
            )

    return {
        "xtquant_installed": True,
        "symbols": normalized_symbols,
        "seconds": seconds,
        "subscribe": subscribe,
        "environment": environment,
        "checks": checks,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QMT xtquant 实时行情能力探测 demo")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help="股票代码，格式如 600000.SH 000001.SZ",
    )
    parser.add_argument(
        "--seconds",
        type=int,
        default=10,
        help="订阅探测等待秒数，仅 --subscribe 时生效",
    )
    parser.add_argument(
        "--subscribe",
        action="store_true",
        help="额外探测 subscribe_quote 推送；默认只做即时拉取",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_probe(args.symbols, seconds=max(0, args.seconds), subscribe=args.subscribe)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
