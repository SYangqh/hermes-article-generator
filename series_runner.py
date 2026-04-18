import json
from pathlib import Path
from datetime import datetime
from hyperextract import Template
import subprocess

ROOT = Path("outputs")
KB_DIR = ROOT / "knowledge_base"
ROOT.mkdir(exist_ok=True)
KB_DIR.mkdir(exist_ok=True)


def create_run_dir(topic: str) -> Path:
    idx = len([d for d in ROOT.iterdir() if d.is_dir()]) + 1
    run_dir = ROOT / f"{str(idx).zfill(3)}_{topic.replace(' ', '_')}"
    run_dir.mkdir()
    return run_dir


def run_article(topic: str, run_dir: Path):
    print(f"\n🚀 生成文章1: {topic}")
    result = subprocess.check_output(
        ["python", "main.py", topic],
        text=True
    )
    final = run_dir / "final.md"
    final.write_text(result, encoding="utf-8")
    return final


def extract_knowledge(md_path: Path):
    print("🧠 Hyper-Extract 抽知识...")
    ka = Template.create("general/knowledge_graph")
    text = md_path.read_text(encoding="utf-8")
    result = ka.parse(text)
    (KB_DIR / "graph.json").write_text(ka.dump(result), encoding="utf-8")


def decide_next_topic() -> str:
    graph_file = KB_DIR / "graph.json"
    if not graph_file.exists():
        return "Java AQS 设计思想"

    graph_text = graph_file.read_text(encoding="utf-8")[:5000]

    prompt = f"""
基于已有知识图谱：
{graph_text}

请选择下一篇最合理的 Java 设计主题。
只输出主题。
"""

    from langchain_openai import ChatOpenAI
    import os

    llm = ChatOpenAI(
        model="qwen-plus",
        openai_api_key=os.getenv("DASHSCOPE_API_KEY"),
        openai_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    return llm.invoke(prompt).content.strip()


if __name__ == "__main__":
    topic = decide_next_topic()
    run_dir = create_run_dir(topic)
    final_md = run_article(topic, run_dir)
    extract_knowledge(final_md)