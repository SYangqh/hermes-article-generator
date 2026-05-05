"""快速文章生成器（精简版）

6 个核心节点：java → [fe ‖ sysconcept] → writer → visual → maintain
只输出 final.md，无中间调试文件。

用法：
    python quick_runner.py              # 生成下一篇
    python quick_runner.py --count 5    # 连续生成 5 篇
    python quick_runner.py --progress   # 查看进度
    python quick_runner.py --reset-errors  # 重置失败文章
"""

import argparse
import re as _re
import traceback
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

ROOT = Path("outputs")
KB_DIR = ROOT / "knowledge_base"
OUTLINE_PATH = ROOT / "outline.json"

ROOT.mkdir(exist_ok=True)
KB_DIR.mkdir(exist_ok=True)

llm = ChatOpenAI(
    model="qwen3.6-plus",
    temperature=0.7,
    openai_api_key="sk-d172b4def726420ea22cfb8aa58ca10a",
    openai_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
)


class ArticleState(TypedDict, total=False):
    """每个 key 独立通道，并行节点各写各的，不冲突。"""
    topic: str
    run_dir: Path
    crossref_hint: str
    java_output: str
    fe_output: str
    analogy_score: str
    sysconcept_output: str
    article_output: str   # writer 输出（完整文章草稿）
    final_output: str     # visual 输出（带图版本）
    ops_output: str       # maintain 输出


@lru_cache(maxsize=None)
def _load_skill(skill_dir: Path, name: str) -> str:
    path = skill_dir / name / "skill.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill not found: {path}")
    return path.read_text(encoding="utf-8")


def _create_article_dir(article: dict) -> Path:
    article_id = article["id"]
    safe_title = article["java_concept"].replace(" ", "_").replace("/", "_")[:20]
    run_dir = ROOT / f"{str(article_id).zfill(3)}_{safe_title}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def generate_article(article: dict, kg_context: str = "",
                     prev_title: str = "", next_title: str = "",
                     crossref_hint: str = "") -> Path:
    from langgraph.graph import StateGraph, END

    skill_dir = Path.home() / ".hermes" / "skills"

    # ── 动态上下文：按难度 + 分类生成，注入每次 LLM 调用 ──
    def _build_dynamic_context() -> str:
        level = article["level"]
        category = article["category"]
        concept = article["java_concept"]

        level_hints = {
            "入门": (
                "【入门级强制要求 — 违反即重写】\n"
                "读者 = 会写 Vue/React，但几乎没写过 Java 的前端工程师。\n"
                "- 每个 Java 术语首次出现必须紧跟括号说明\n"
                "- 需要代码示例\n"
                "- 禁止出现（入门）：vtable、invokevirtual、Liskov 替换原则、脆弱基类、\n"
                "  协变/逆变、设计模式名词（未当场解释时）、@PostConstruct（未解释时）、\n"
                "  Bean 生命周期（未解释时）、依赖注入容器（未解释时）\n"
                "- 踩坑需要讲一下，必须是初学者真实会犯的错\n"
                "- 文章目标：读完能写出一个可运行的最小示例"
            ),
            "进阶": (
                "【进阶级要求】\n"
                "读者 = 写过 3 个月以上 Java，熟悉基础语法，接触过 Spring Boot。\n"
                "- Java 高级术语首次出现需简短解释\n"
                "- 可以用 Spring Boot，但要解释框架在做什么\n"
                "- 可以提设计原则，但必须用具体代码举例\n"
                "- 禁止出现（进阶）：vtable、invokevirtual、协变/逆变、字节码指令细节\n"
                "- 文章目标：读完能在真实项目中做出正确选择"
            ),
            "高级": (
                "【高级级要求】\n"
                "读者 = 有 1 年以上 Java 经验，接触过并发/框架源码。\n"
                "- 可以进入框架源码和 JVM 行为分析\n"
                "- 必须有工程场景牵引，不堆砌理论\n"
                "- 文章目标：读完能在架构决策中权衡利弊"
            ),
        }

        category_hints = {
            "基础语法": "侧重「Java 和 JavaScript/TypeScript 的关键差异」，每个语法点回答「为什么 Java 这样规定」。",
            "面向对象": "侧重「OOP 在大型项目协作中解决了什么问题」，用多人协作场景举例。",
            "集合框架": "侧重「选哪种集合」的决策能力，用时间/空间权衡说服读者。",
            "并发编程": "侧重「为什么需要这个并发机制」，每个概念配一个「不用它会出什么事」的反例。",
            "设计模式": "侧重「这个模式在 Spring 中的真实用法」，不用动物/形状等教科书例子。",
            "函数式编程": "侧重「Stream/Lambda 比传统写法好在哪」，用代码对比说服读者。",
            "异常处理": "侧重「checked vs unchecked 的设计争议」，让读者理解代价和收益。",
            "Spring框架": "侧重「Spring 用 Java 的哪些特性实现了什么魔法」，帮读者从使用者升级为理解者。",
            "数据持久化": "侧重「ORM vs 原生 SQL 的工程权衡」，用真实性能/维护场景举例。",
            "IO与NIO": "侧重「阻塞 vs 非阻塞的本质区别」，用连接数/吞吐量场景让读者感受差异。",
            "JVM原理": "侧重「这个 JVM 机制对你的代码行为有什么影响」，不要纯讲规范。",
            "性能优化": "侧重「怎么定位问题」而非「怎么背参数」，给出可操作的排查流程。",
            "微服务架构": "侧重「分布式带来了哪些单体不存在的问题」，用一致性/可用性场景举例。",
            "网络与Netty": "侧重「为什么 Java 网络编程需要 Netty」，从 BIO 痛点引入。",
        }

        parts = [
            "📌 当前文章动态上下文（严格按此执行，不可绕过）：",
            f"  概念：{concept}  |  分类：{category}  |  难度：{level}",
            "",
            level_hints.get(level, level_hints["进阶"]),
            "",
            category_hints.get(category, ""),
        ]
        return "\n".join(p for p in parts if p)

    dynamic_ctx = _build_dynamic_context()

    def run_skill(skill_name: str, content: str) -> str:
        system_prompt = _load_skill(skill_dir, skill_name)
        # System 消息：skill 提示词 + 动态上下文（API prefix cache 友好）
        # Human 消息：本次任务内容
        messages = [
            SystemMessage(content=f"{system_prompt}\n\n---\n\n{dynamic_ctx}"),
            HumanMessage(content=content),
        ]
        return llm.invoke(messages).content

    # ──────────────────────────────────────────
    # 节点 1：Java Expert
    # ──────────────────────────────────────────
    def java_node(state):
        print("  ▶ Java Expert")
        kg_section = f"{kg_context}\n\n" if kg_context else ""
        topic_prompt = (
            f"{kg_section}"
            f"当前文章任务：\n"
            f"  Java概念：{article['java_concept']}\n"
            f"  分类：{article['category']}  难度：{article['level']}\n"
            f"  写作背景：{article['topic']}\n\n"
            f"请以「{article['java_concept']}」为核心，深入解析：\n"
            f"1. 真实 Java 项目中会遇到的工程问题\n"
            f"2. Java 为什么这样设计（工程权衡）\n"
            f"3. 核心用法和 {article['level']} 程度开发者最容易误解的地方\n\n"
            f"注意：只从 Java 工程师视角写，不涉及前端类比。"
        )
        return {"java_output": run_skill("java-expert", topic_prompt)}

    # ──────────────────────────────────────────
    # 节点 2 & 3：Frontend Expert + SysConcept（并行）
    # ──────────────────────────────────────────
    def fe_node(state):
        print("  ▶ Frontend Expert")
        result = run_skill("frontend-expert", state["java_output"])
        analogy_score = ""
        for line in result.splitlines()[:5]:
            if "契合度" in line:
                analogy_score = line.strip()
                break
        if analogy_score:
            print(f"     {analogy_score}")
        return {"fe_output": result, "analogy_score": analogy_score}

    def sysconcept_node(state):
        print("  ▶ SysConcept")
        result = run_skill("sysconcept", state["java_output"])
        if "NOT_APPLICABLE" in result:
            print("     ⏭ 无系统层内容")
            return {"sysconcept_output": ""}
        return {"sysconcept_output": result}

    # ──────────────────────────────────────────
    # 节点 4：Writer（整合成完整文章，使用 educator skill）
    # ──────────────────────────────────────────
    def writer_node(state):
        print("  ▶ Writer（整合成文）")
        analogy_score = state.get("analogy_score", "")
        crossref_note = state.get("crossref_hint", "")
        sysconcept_section = (
            f"\n\n---\n## 系统层内容（请融入「动手验证」节）\n\n{state.get('sysconcept_output', '')}"
            if state.get("sysconcept_output") else ""
        )
        crossref_section = f"\n\n{crossref_note}" if crossref_note else ""

        # 明确告知字数要求和难度
        word_count_req = {
            "入门": "字数要求：1500-2000 字（不含代码）。入门读者注意力有限，宁可短而精。",
            "进阶": "字数要求：2000-2800 字（不含代码）。",
            "高级": "字数要求：2500-3500 字（不含代码）。高级文章可以更深入。",
        }.get(article["level"], "字数要求：2000-2800 字（不含代码）。")

        merged = (
            f"文章难度：{article['level']}（严格按难度控制深度和术语）\n"
            f"前端类比契合度：{analogy_score or '未评估'}\n"
            f"{word_count_req}\n"
            f"{crossref_section}\n\n"
            f"---\n## Java 专家素材\n\n{state['java_output']}\n\n"
            f"---\n## 前端类比素材\n\n{state['fe_output']}"
            f"{sysconcept_section}"
        )
        return {"article_output": run_skill("educator", merged)}

    # ──────────────────────────────────────────
    # 节点 5：Visual Architect（插入 Mermaid 图）
    # ──────────────────────────────────────────
    def visual_node(state):
        print("  ▶ Visual Architect")
        result = run_skill("visual-architect", state["article_output"])
        return {"final_output": result}

    # ──────────────────────────────────────────
    # 节点 6：Maintainer（系列导航）
    # ──────────────────────────────────────────
    def maintain_node(state):
        print("  ▶ Maintainer")
        nav_context = (
            f"当前文章 ID: {article['id']}\n"
            f"当前文章标题: {article['title']}\n"
            f"上一篇标题: {prev_title or '（系列第一篇）'}\n"
            f"下一篇标题: {next_title or '（系列最后一篇）'}\n\n"
            f"以下是文章正文，请生成系列导航块：\n\n"
        )
        result = run_skill("maintainer", nav_context + state["final_output"])
        return {"ops_output": result}

    # ── 构建图 ──
    graph = StateGraph(ArticleState)
    for name, fn in [
        ("java", java_node),
        ("fe", fe_node),
        ("sysconcept", sysconcept_node),
        ("writer", writer_node),
        ("visual", visual_node),
        ("maintain", maintain_node),
    ]:
        graph.add_node(name, fn)

    graph.set_entry_point("java")
    # fe 和 sysconcept 均只依赖 java_output，并行执行
    graph.add_edge("java", "fe")
    graph.add_edge("java", "sysconcept")
    # writer 等待 fe + sysconcept 全部完成（fan-in）
    graph.add_edge("fe", "writer")
    graph.add_edge("sysconcept", "writer")
    graph.add_edge("writer", "visual")
    graph.add_edge("visual", "maintain")
    graph.add_edge("maintain", END)
    app = graph.compile()

    run_dir = _create_article_dir(article)
    result = app.invoke({
        "topic": article["title"],
        "run_dir": run_dir,
        "crossref_hint": crossref_hint,
    })

    # ── 解析 maintainer 输出：正文 + 导航块 → final.md ──
    article_body = result["final_output"].rstrip()
    ops_raw = result.get("ops_output", "")

    # 清理 visual-architect / diagram-reviewer 留下的元数据注释
    article_body = _re.sub(r'<!--\s*DIAGRAM:\s*\w+\s*-->', '', article_body).strip()
    # 清理 bridge-check 注释（如果 visual 把它带过来了）
    article_body = _re.sub(r'<!--\s*bridge-check:.*?-->', '', article_body, flags=_re.DOTALL).strip()

    nav_match = _re.search(
        r'(---\s*\n### 系列导航.*?)(?=\n*---\s*REVIEW_START|$)',
        ops_raw, _re.DOTALL
    )
    nav_part = nav_match.group(1).strip() if nav_match else ""

    final_content = article_body
    if nav_part:
        final_content += "\n\n" + nav_part

    final_path = run_dir / "final.md"
    final_path.write_text(final_content, encoding="utf-8")

    return final_path


# ─────────────────────────────────────────────
# 主运行逻辑
# ─────────────────────────────────────────────

def show_last() -> None:
    """打印最新生成的 final.md 内容，供 Hermes 直接读取。"""
    candidates = sorted(ROOT.glob("*/final.md"))
    if not candidates:
        print("❌ 还没有生成过任何文章。")
        return
    latest = candidates[-1]
    print(f"📄 {latest}\n")
    print(latest.read_text(encoding="utf-8"))


def show_outline() -> None:
    """打印大纲摘要（id / 标题 / 状态）。"""
    from outline import OutlineManager
    outline = OutlineManager(OUTLINE_PATH, llm)
    if not outline.articles:
        print("❌ 大纲不存在，请先运行：python series_runner.py --outline")
        return
    for a in outline.articles:
        status = a.get("status", "pending")
        icon = {"done": "✅", "generating": "⏳", "error": "❌"}.get(status, "⬜")
        print(f"{icon} [{a['id']:>3}] {a['title']}  ({a['category']} / {a['level']})")


def run_by_id(article_id: int, force: bool = False) -> None:
    """按 ID 生成（或强制重跑）指定文章。"""
    from outline import OutlineManager
    outline = OutlineManager(OUTLINE_PATH, llm)
    if not outline.articles:
        print("❌ 大纲不存在，请先运行：python series_runner.py --outline")
        return

    article = next((a for a in outline.articles if a["id"] == article_id), None)
    if not article:
        print(f"❌ 未找到 ID={article_id} 的文章")
        return

    if article.get("status") == "done" and not force:
        print(f"⚠️  文章 {article_id} 已生成完毕（用 --force 强制重跑）")
        return

    print(f"\n{'='*60}")
    print(f"🚀 [{article['id']}/{len(outline.articles)}] {article['title']}")
    print(f"   分类: {article['category']}  难度: {article['level']}")
    print(f"{'='*60}")

    outline.mark_generating(article["id"])
    try:
        all_articles = outline.articles
        idx = next((i for i, a in enumerate(all_articles) if a["id"] == article["id"]), None)
        prev_title = all_articles[idx - 1]["title"] if idx and idx > 0 else ""
        next_title = all_articles[idx + 1]["title"] if idx is not None and idx < len(all_articles) - 1 else ""

        final_path = generate_article(article, "", prev_title, next_title, "")
        print(f"  ✅ 已保存: {final_path}")
        outline.mark_done(article["id"], final_path)
    except Exception as e:
        print(f"  ❌ 生成失败: {e}")
        traceback.print_exc()
        outline.mark_error(article["id"], str(e))


def run_next(count: int = 1):
    from outline import OutlineManager

    outline = OutlineManager(OUTLINE_PATH, llm)
    if not outline.articles:
        print("❌ 大纲不存在，请先运行：python series_runner.py --outline")
        return

    generated = 0

    for _ in range(count):
        article = outline.next_pending()
        if not article:
            print("🎉 所有文章已生成完毕！")
            break

        print(f"\n{'='*60}")
        print(f"🚀 [{article['id']}/{len(outline.articles)}] {article['title']}")
        print(f"   分类: {article['category']}  难度: {article['level']}")
        print(f"   指令: {article['topic']}")
        print(f"{'='*60}")

        outline.mark_generating(article["id"])

        try:
            all_articles = outline.articles
            idx = next((i for i, a in enumerate(all_articles) if a["id"] == article["id"]), None)
            prev_title = all_articles[idx - 1]["title"] if idx and idx > 0 else ""
            next_title = all_articles[idx + 1]["title"] if idx is not None and idx < len(all_articles) - 1 else ""

            final_path = generate_article(article, "", prev_title, next_title, "")
            print(f"  ✅ 已保存: {final_path}")

            outline.mark_done(article["id"], final_path)
            generated += 1

        except Exception as e:
            print(f"  ❌ 生成失败: {e}")
            traceback.print_exc()
            outline.mark_error(article["id"], str(e))

    outline.print_progress()
    print(f"\n本次生成了 {generated} 篇文章。")


# ─────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="快速文章生成器（精简版）")
    parser.add_argument("--count", type=int, default=1, help="连续生成文章数（默认 1）")
    parser.add_argument("--id", type=int, default=None, help="按 ID 生成指定文章")
    parser.add_argument("--force", action="store_true", help="与 --id 配合，强制重跑已完成的文章")
    parser.add_argument("--progress", action="store_true", help="查看进度")
    parser.add_argument("--show-last", action="store_true", help="打印最新生成的文章内容")
    parser.add_argument("--show-outline", action="store_true", help="打印大纲摘要")
    parser.add_argument("--reset-errors", action="store_true", help="重置失败文章")
    args = parser.parse_args()

    from outline import OutlineManager
    outline = OutlineManager(OUTLINE_PATH, llm)

    if args.progress:
        outline.print_progress()
    elif args.reset_errors:
        outline.reset_errors()
        outline.print_progress()
    elif args.show_last:
        show_last()
    elif args.show_outline:
        show_outline()
    elif args.id is not None:
        run_by_id(args.id, force=args.force)
    else:
        if not outline.articles:
            print("❌ 大纲不存在，请先运行：python series_runner.py --outline")
        else:
            run_next(count=args.count)
