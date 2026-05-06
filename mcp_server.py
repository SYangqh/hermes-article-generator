"""
Article Workflow MCP Server

以 FastMCP stdio 模式运行，供 Hermes Agent 的 native-mcp 客户端接入。
注册后工具名前缀：mcp_article_runner_*

注册方式：在 ~/.hermes/config.yaml 加入：

  mcp_servers:
    article_runner:
      command: "/home/ysysq/langchain-env/bin/python"
      args: ["/home/ysysq/article-workflow/mcp_server.py"]
      timeout: 660
      connect_timeout: 30

然后 /reload 或重启 Hermes。
"""

import json
import subprocess
import sys
from pathlib import Path

from fastmcp import FastMCP

ROOT = Path(__file__).parent.resolve()
PYTHON = sys.executable
MODEL_CONFIG = ROOT / "outputs" / "model_config.json"
DEFAULT_MODEL = "qwen3.6-flash"

mcp = FastMCP("article_runner")


def _run(args: list[str], timeout: int = 600) -> str:
    """用当前 venv Python 跑 quick_runner.py，返回合并后的输出。"""
    result = subprocess.run(
        [PYTHON, "quick_runner.py"] + args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = result.stdout.strip()
    if result.returncode != 0 and result.stderr:
        out += f"\n[stderr]\n{result.stderr.strip()}"
    return out or "(no output)"


@mcp.tool
def run_next() -> str:
    """生成大纲中下一篇待生成的文章。"""
    return _run([])


@mcp.tool
def run_by_id(article_id: int) -> str:
    """按 ID 生成指定文章。article_id: 文章编号（见 show_outline）。"""
    return _run(["--id", str(article_id)])


@mcp.tool
def rerun(article_id: int) -> str:
    """强制重跑指定 ID 的文章，即使已生成完毕也会重新生成。"""
    return _run(["--id", str(article_id), "--force"])


@mcp.tool
def run_count(count: int) -> str:
    """连续生成 N 篇文章。count: 数量（1-10）。"""
    if not 1 <= count <= 10:
        return "错误：count 范围是 1-10"
    return _run(["--count", str(count)], timeout=count * 660)


@mcp.tool
def progress() -> str:
    """查看文章系列的生成进度概览。"""
    return _run(["--progress"])


@mcp.tool
def show_outline() -> str:
    """列出大纲中所有文章及其状态（✅已完成 / ⬜待生成 / ❌失败）。"""
    return _run(["--show-outline"])


@mcp.tool
def show_last() -> str:
    """返回最新生成的文章完整 Markdown 内容。"""
    return _run(["--show-last"])


@mcp.tool
def reset_errors() -> str:
    """将所有失败（error）状态的文章重置为待生成。"""
    return _run(["--reset-errors"])


@mcp.tool
def get_model() -> str:
    """【唯一正确做法】当用户问"当前模型是什么"/"用的什么模型"/"模型名"时，必须调用此工具。
    直接返回 outputs/model_config.json 中记录的模型名称，不要猜测、不要推断。
    """
    if MODEL_CONFIG.exists():
        try:
            model = json.loads(MODEL_CONFIG.read_text(encoding="utf-8")).get("model", DEFAULT_MODEL)
            return f"当前模型：{model}"
        except Exception:
            pass
    return f"当前模型：{DEFAULT_MODEL}（默认，配置文件不存在）"


@mcp.tool
def set_model(model: str) -> str:
    """切换文章生成使用的模型。下次生成文章时自动生效。
    调用时机：用户说"切换模型"/"换模型"/"用xxx模型"/"set model xxx"。

    常用模型：
    - qwen3.6-flash
    - qwen3.6-flash-2026-04-16
    - qwen3.6-plus
    - qwen-plus
    """
    MODEL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    MODEL_CONFIG.write_text(
        json.dumps({"model": model}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return f"✅ 模型已切换为：{model}（下次生成时生效）"


if __name__ == "__main__":
    mcp.run()  # stdio 模式（默认），Hermes 通过 stdin/stdout 与之通信
