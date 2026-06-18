import os
from pathlib import Path
import shutil
import subprocess


DEFAULT_MODEL = "deepseek-r1:7b"
DEFAULT_TRAECLI_MODEL = "DeepSeek-V4-Pro"
DEFAULT_TRAECLI_TIMEOUT_SECONDS = 300


def _build_prompt(strong_text: str, dip_text: str, trade_date: str) -> str:
    return f"""
你是A股短线分析师，今天分析的是 {trade_date} 的市场数据。

请基于给定数据做辅助分析，不要编造未提供的数据。输出必须简洁、专业，并提醒市场风险。

=== 一、优势股 ===
{strong_text}

=== 二、抄底候选（板块 + 个股） ===
{dip_text}

请分别针对两类数据给出分析：

【优势股分析】
1. 哪几只最值得关注？理由是什么（结合涨幅、换手率、成交量）？
2. 主要风险点？
3. 短线操作建议（买入时机、止损位）

【抄底分析】
1. 哪个板块最具反弹潜力？为什么？
2. 板块内哪只个股最值得抄底？
3. 抄底风险提示（是否存在继续下跌风险）
4. 建议的介入策略（分批建仓 or 等企稳信号）

请用中文回答，每条建议控制在2~3句话。最后补充一句：以上为量化筛选后的辅助分析，不构成投资建议。
""".strip()


def analyze_stocks(strong_text: str, dip_text: str, trade_date: str) -> str:
    provider = os.getenv("AI_PROVIDER", "traecli").strip().lower()
    prompt = _build_prompt(strong_text, dip_text, trade_date)

    if provider in {"ollama", "local"}:
        return _analyze_with_ollama(prompt)

    if provider in {"trae", "traecli", "trae-cli"}:
        return _analyze_with_traecli(prompt)

    raise RuntimeError(f"不支持的 AI_PROVIDER：{provider}，可选 traecli 或 ollama")


def _analyze_with_ollama(prompt: str) -> str:
    try:
        import ollama
    except ImportError as exc:
        raise RuntimeError("缺少 ollama 依赖，请先运行：pip install -r requirements.txt") from exc

    model = os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)

    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        raise RuntimeError(
            f"Ollama 调用失败，请确认已启动 Ollama，并已拉取模型：ollama pull {model}"
        ) from exc

    return response["message"]["content"]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _traecli_disallowed_tools():
    raw = os.getenv(
        "TRAECLI_DISALLOWED_TOOLS",
        "Bash,Edit,Replace,Write,MultiEdit,NotebookEdit",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


def _build_traecli_env(token: str):
    env = os.environ.copy()
    env["TRAECLI_PERSONAL_ACCESS_TOKEN"] = token

    home = env.get("HOME") or str(Path.home()) or "/root"
    if not home or home == "/":
        home = "/root"
    env["HOME"] = home
    env.setdefault("XDG_CACHE_HOME", str(Path(home) / ".cache"))
    env.setdefault("XDG_CONFIG_HOME", str(Path(home) / ".config"))
    env.setdefault("XDG_DATA_HOME", str(Path(home) / ".local" / "share"))
    env["PATH"] = f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin:{env.get('PATH', '')}"

    for dirname in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        Path(env[dirname]).mkdir(parents=True, exist_ok=True)

    use_proxy = os.getenv("TRAECLI_USE_PROXY", "").strip().lower() in {"1", "true", "yes", "on"}
    if not use_proxy:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)

    return env


def _clean_traecli_error(stderr: str, stdout: str, returncode: int) -> str:
    lines = []
    for line in (stderr or "").splitlines():
        if "failed to create token store" in line and "keyring is not supported" in line:
            continue
        if "attempting token-based login" in line:
            continue
        if "successfully logged in with personal access token" in line:
            continue
        lines.append(line)

    detail = "\n".join(lines).strip() or (stdout or "").strip()
    return detail or f"退出码 {returncode}"


def _run_traecli(command, prompt: str, env, timeout_seconds: int, pass_prompt_as_arg: bool = False):
    run_command = [*command, prompt] if pass_prompt_as_arg else command
    return subprocess.run(
        run_command,
        input=None if pass_prompt_as_arg else prompt,
        text=True,
        capture_output=True,
        timeout=timeout_seconds + 30,
        env=env,
        check=False,
    )


def _analyze_with_traecli(prompt: str) -> str:
    token = os.getenv("TRAECLI_PERSONAL_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("缺少 TRAECLI_PERSONAL_ACCESS_TOKEN 环境变量，无法调用 TRAE CLI")

    binary = os.getenv("TRAECLI_BIN", "traecli")
    executable = shutil.which(binary)
    if not executable and "/" not in binary:
        fallback_paths = [
            Path.home() / ".local" / "bin" / binary,
            Path("/root/.local/bin") / binary,
            Path("/usr/local/bin") / binary,
            Path("/usr/bin") / binary,
        ]
        executable = next((str(path) for path in fallback_paths if path.exists()), None)
    if not executable:
        raise RuntimeError(
            "未找到 traecli 可执行文件，请先安装 TRAE CLI 并确认 PATH 配置正确；"
            "systemd 环境建议在 .env 中配置 TRAECLI_BIN=/root/.local/bin/traecli"
        )

    timeout_seconds = _env_int("TRAECLI_TIMEOUT_SECONDS", DEFAULT_TRAECLI_TIMEOUT_SECONDS)
    timeout_seconds = max(30, min(timeout_seconds, 1800))

    guarded_prompt = (
        "你只需要基于用户提供的行情文本直接生成分析，不要读取文件、不要执行命令、不要修改代码。"
        "如果需要额外数据，请明确说明当前数据不足，不要调用工具。\n\n"
        f"{prompt}"
    )

    command = [
        executable,
        "-c",
        f"default_model={os.getenv('TRAECLI_MODEL', DEFAULT_TRAECLI_MODEL)}",
        "--print",
        "--output-format",
        "text",
        "--query-timeout",
        f"{timeout_seconds}s",
    ]
    for tool in _traecli_disallowed_tools():
        command.extend(["--disallowed-tool", tool])

    env = _build_traecli_env(token)

    try:
        result = _run_traecli(command, guarded_prompt, env, timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"TRAE CLI 调用超时（>{timeout_seconds} 秒）") from exc

    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()

    if result.returncode != 0 and not output:
        try:
            retry_result = _run_traecli(command, guarded_prompt, env, timeout_seconds, pass_prompt_as_arg=True)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"TRAE CLI 调用超时（>{timeout_seconds} 秒）") from exc

        retry_output = (retry_result.stdout or "").strip()
        if retry_result.returncode == 0 and retry_output:
            return retry_output

        retry_error = (retry_result.stderr or "").strip()
        if retry_error:
            error = f"{error}\n{retry_error}".strip()
        if retry_output:
            output = retry_output
        result = retry_result

    if result.returncode != 0:
        detail = _clean_traecli_error(error, output, result.returncode)
        raise RuntimeError(f"TRAE CLI 调用失败：{detail}")

    if not output:
        detail = error or "无输出"
        raise RuntimeError(f"TRAE CLI 未返回分析结果：{detail}")

    return output
