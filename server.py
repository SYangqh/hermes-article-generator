"""
article-workflow HTTP 服务

把 quick_runner.py 的所有命令暴露为 REST 接口，
供飞书机器人、Webhook、或任何外部调用方使用。

启动：
    source ~/langchain-env/bin/activate
    uvicorn server:app --host 0.0.0.0 --port 8000

接口：
    POST /run               → 生成下一篇
    POST /run/{id}          → 生成指定 ID 的文章
    POST /rerun/{id}        → 强制重跑（--force）
    GET  /progress          → 进度概览
    GET  /outline           → 大纲列表
    GET  /last              → 最新文章内容
    POST /reset-errors      → 重置失败文章
"""

import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

ROOT = Path(__file__).parent.resolve()
PYTHON = sys.executable   # 当前虚拟环境的 Python，避免 source 问题


def _run(args: list[str], timeout: int = 600) -> str:
    """在 ROOT 目录下，用当前 venv Python 执行 quick_runner.py。"""
    cmd = [PYTHON, "quick_runner.py"] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout
        if result.returncode != 0:
            output += f"\n[stderr]\n{result.stderr}"
        return output.strip()
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="命令超时（超过 10 分钟）")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app = FastAPI(
    title="Article Workflow API",
    description="quick_runner.py 的 HTTP 封装，供飞书机器人等外部服务调用",
    version="1.0.0",
)


@app.post("/run", summary="生成下一篇待生成文章")
def run_next():
    output = _run([])
    return {"output": output}


@app.post("/run/{article_id}", summary="生成指定 ID 的文章")
def run_by_id(article_id: int):
    output = _run(["--id", str(article_id)])
    return {"output": output}


@app.post("/rerun/{article_id}", summary="强制重跑指定 ID（已完成也重新生成）")
def rerun(article_id: int):
    output = _run(["--id", str(article_id), "--force"])
    return {"output": output}


@app.post("/run-count/{count}", summary="连续生成 N 篇")
def run_count(count: int):
    if count < 1 or count > 20:
        raise HTTPException(status_code=400, detail="count 范围：1-20")
    output = _run(["--count", str(count)], timeout=count * 600)
    return {"output": output}


@app.get("/progress", summary="进度概览")
def progress():
    output = _run(["--progress"])
    return {"output": output}


@app.get("/outline", summary="大纲列表（含状态）")
def outline():
    output = _run(["--show-outline"])
    return {"output": output}


@app.get("/last", summary="最新生成文章的 Markdown 内容")
def show_last():
    output = _run(["--show-last"])
    return PlainTextResponse(output)


@app.post("/reset-errors", summary="重置所有失败文章为待生成")
def reset_errors():
    output = _run(["--reset-errors"])
    return {"output": output}


@app.get("/", summary="健康检查")
def health():
    return {"status": "ok", "service": "article-workflow"}
