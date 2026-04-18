import os
import sys
from pathlib import Path
from datetime import datetime
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from hyperextract import Template

# ========= LLM =========
llm = ChatOpenAI(
    model="qwen-plus",
    temperature=0.7,
    openai_api_key=os.getenv("DASHSCOPE_API_KEY"),
    openai_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# ========= 目录 =========
SKILL_DIR = Path.home() / ".hermes" / "skills"
ROOT_OUTPUT = Path("outputs")
KB_DIR = ROOT_OUTPUT / "knowledge_base"

ROOT_OUTPUT.mkdir(exist_ok=True)
KB_DIR.mkdir(exist_ok=True)


# ========= 🏭 每次独立 run 目录 =========
def create_run_dir(topic: str) -> Path:
    existing = sorted(
        [d for d in ROOT_OUTPUT.iterdir() if d.is_dir() and d.name[:3].isdigit()]
    )
    index = len(existing) + 1
    run_dir = ROOT_OUTPUT / f"{str(index).zfill(3)}_{topic.replace(' ', '_')}"
    run_dir.mkdir()
    return run_dir


# ========= 🧠 自动选下一篇主题 =========
def decide_next_topic() -> str:
    graph_file = KB_DIR / "graph.json"
    if not graph_file.exists():
        return "Java"

    graph_text = graph_file.read_text(encoding="utf-8")

    prompt = f"""
你正在构建一个《Java 设计思想 → 前端认知映射》系列文章。

以下是已经写过的知识图谱：
{graph_text[:6000]}

请选择**下一篇最合理的 Java 设计知识点**作为主题。
只输出主题名称。
"""
    return llm.invoke(prompt).content.strip()


# ========= 工具函数 =========
def load_skill(name: str) -> str:
    path = SKILL_DIR / name / "skill.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill not found: {path}")
    return path.read_text(encoding="utf-8")


def save_step(run_dir: Path, name: str, content: str):
    (run_dir / f"{name}.md").write_text(content, encoding="utf-8")


def run_skill(skill_name: str, content: str) -> str:
    system_prompt = load_skill(skill_name)

    publish_guard = """
⚠️ 当前为最终发布模式：
禁止输出任何 审核 / 测试 / 专家评审 / 结论验证 等内容。
只允许输出可直接发布的文章正文。
"""

    prompt = f"""{system_prompt}

{publish_guard}

====================
以下是需要处理的内容：
====================

{content}
"""
    return llm.invoke(prompt).content


# ========= 🧠 知识回灌 =========
def recall_memory() -> str:
    graph_file = KB_DIR / "graph.json"
    if not graph_file.exists():
        return ""

    graph_text = graph_file.read_text(encoding="utf-8")

    return f"""
🧠 以下是历史文章沉淀的知识图谱，请避免重复基础讲解，
在此基础上延伸新的设计思想，并保持系列连贯：

{graph_text[:4000]}
"""


# ========= LangGraph 节点（完全不改你的逻辑，只加保存） =========
def java_node(state):
    print("▶ Java Expert")
    result = run_skill(
        "java-expert",
        f"{recall_memory()}\n主题：{state['topic']}\n请选择最合适的 Java 设计知识点并深入解析。"
    )
    save_step(state["run_dir"], "01_java_expert", result)
    return {"topic": state["topic"], "run_dir": state["run_dir"], "java_output": result}


def fe_node(state):
    print("▶ Frontend Expert")
    result = run_skill("frontend-expert", state["java_output"])
    save_step(state["run_dir"], "02_frontend_expert", result)
    return {**state, "fe_output": result}


def edu_node(state):
    print("▶ Educator")
    merged = state["java_output"] + "\n\n" + state["fe_output"]
    result = run_skill("educator", merged)
    save_step(state["run_dir"], "03_educator", result)
    return {**state, "edu_output": result}


def fmt_node(state):
    print("▶ Formatter")
    result = run_skill("formatter", state["edu_output"])
    save_step(state["run_dir"], "04_formatter", result)
    return {**state, "fmt_output": result}


def ops_node(state):
    print("▶ Maintainer")
    result = run_skill("maintainer", state["fmt_output"])
    save_step(state["run_dir"], "05_final_article", result)
    return {"final_output": result, "run_dir": state["run_dir"]}


# ========= 构建 Graph（完全不动） =========
graph = StateGraph(dict)
graph.add_node("java", java_node)
graph.add_node("fe", fe_node)
graph.add_node("edu", edu_node)
graph.add_node("fmt", fmt_node)
graph.add_node("ops", ops_node)

graph.set_entry_point("java")
graph.add_edge("java", "fe")
graph.add_edge("fe", "edu")
graph.add_edge("edu", "fmt")
graph.add_edge("fmt", "ops")
graph.add_edge("ops", END)

app = graph.compile()


# ========= Hyper-Extract =========
def extract_knowledge(md_path: Path):
    print("\n🧠 Hyper-Extract: 更新知识库中...\n")

    ka = Template.create("general/knowledge_graph")
    text = md_path.read_text(encoding="utf-8")
    result = ka.parse(text)

    (KB_DIR / "graph.json").write_text(
        ka.dump(result),
        encoding="utf-8"
    )


# ========= 运行 =========
if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else decide_next_topic()

    run_dir = create_run_dir(topic)

    print(f"\n🚀 Generating article: {topic}\n📁 输出目录: {run_dir}\n")

    result = app.invoke({"topic": topic, "run_dir": run_dir})
    article = result["final_output"]

    final_path = run_dir / "final.md"
    final_path.write_text(article, encoding="utf-8")

    print(f"\n✅ 已保存: {final_path}")

    extract_knowledge(final_path)