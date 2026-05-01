"""系列文章运行器

按 outline.json 大纲顺序，逐篇驱动 LangGraph 流水线生成文章，
每篇完成后自动更新知识图谱，并将 KG 上下文注入下一篇。

用法：
    python series_runner.py              # 按大纲生成下一篇
    python series_runner.py --count 5    # 连续生成 5 篇
    python series_runner.py --outline    # 仅生成/查看大纲
    python series_runner.py --progress   # 查看进度
    python series_runner.py --reset-errors  # 重置失败文章
"""

import argparse
import traceback
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

ROOT = Path("outputs")
KB_DIR = ROOT / "knowledge_base"
OUTLINE_PATH = ROOT / "outline.json"
KG_PATH = KB_DIR / "graph.json"

ROOT.mkdir(exist_ok=True)
KB_DIR.mkdir(exist_ok=True)


class ArticleState(TypedDict, total=False):
    """LangGraph 状态：每个 key 独立通道，并行节点只写自己的 key，互不干扰。"""
    topic: str
    run_dir: Path
    crossref_hint: str
    java_output: str
    fe_output: str
    analogy_score: str
    practitioner_output: str
    sysconcept_output: str
    edu_output: str
    fmt_output: str
    ops_output: str
    final_output: str
    review_critique: str
    fact_check: str


# ─────────────────────────────────────────────
# LLM（所有模块共用）
# ─────────────────────────────────────────────

llm = ChatOpenAI(
    model="qwen3.6-plus",
    temperature=0.7,
    openai_api_key="sk-d172b4def726420ea22cfb8aa58ca10a",
    openai_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
)


# ─────────────────────────────────────────────
# 单篇文章生成
# ─────────────────────────────────────────────

def _create_article_dir(article: dict) -> Path:
    """为大纲中的文章创建固定输出目录（按 id 编号）。"""
    article_id = article["id"]
    safe_title = article["java_concept"].replace(" ", "_").replace("/", "_")[:20]
    run_dir = ROOT / f"{str(article_id).zfill(3)}_{safe_title}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


@lru_cache(maxsize=None)
def _load_skill(skill_dir: Path, name: str) -> str:
    """读取 skill 提示词，进程内按 (skill_dir, name) 缓存，批量生成时只读一次磁盘。"""
    path = skill_dir / name / "skill.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill not found: {path}")
    return path.read_text(encoding="utf-8")


def generate_article(article: dict, kg_context: str = "",
                     prev_title: str = "", next_title: str = "",
                     crossref_hint: str = "") -> Path:
    """调用 LangGraph 流水线生成单篇文章，返回 final.md 路径。"""
    from langgraph.graph import StateGraph, END

    skill_dir = Path.home() / ".hermes" / "skills"

    # ── 动态上下文：根据文章元数据生成，注入每个 skill ──
    def _build_dynamic_context() -> str:
        """根据当前文章的 category / level / java_concept 生成动态写作指引。"""
        level = article['level']
        category = article['category']
        concept = article['java_concept']

        # 按难度级别调整
        level_hints = {
            "入门": (
                "读者是刚接触 Java 的前端工程师，所有术语首次出现必须给一句话定义。\n"
                "代码示例用最简单的 main 方法或 Spring Boot Controller，不要用框架内部 API。\n"
                "全文目标：读完能动手写出一个可运行的示例。"
            ),
            "进阶": (
                "读者已写过 Spring Boot 项目，了解基础语法。\n"
                "可以讨论框架原理和设计选择，但必须有完整解释链，不跳步骤。\n"
                "全文目标：读完能理解「为什么这样设计」并在项目中做出正确选择。"
            ),
            "高级": (
                "读者有 1-2 年 Java 经验，接触过并发/框架源码。\n"
                "可以进入源码级分析和 JVM 行为，但必须有工程场景牵引，不堆砌理论。\n"
                "全文目标：读完能在架构决策中权衡利弊。"
            ),
        }

        # 按分类调整写作侧重
        category_hints = {
            "基础语法": "侧重「Java 和其他语言的关键差异」，每个语法点都要回答「为什么 Java 这样规定」。",
            "面向对象": "侧重「OOP 在大型项目中解决了什么协作问题」，用真实的多人协作场景举例。",
            "集合框架": "侧重「选择哪种集合」的决策能力，用数据结构的时间/空间权衡说服读者。",
            "并发编程": "侧重「为什么需要这个并发原语」，每个概念必须配一个「不用它会出什么事」的反例。",
            "设计模式": "侧重「这个模式在 Spring 生态中的真实用法」，不要用动物/形状等教科书例子。",
            "函数式编程": "侧重「Stream/Lambda 比传统写法好在哪」，用代码对比（传统 vs 函数式）说服读者。",
            "异常处理": "侧重「checked vs unchecked 的设计争议」，让读者理解 Java 异常设计的代价和收益。",
            "Spring框架": "侧重「Spring 用 Java 的哪些特性实现了什么魔法」，帮读者从使用者升级为理解者。",
            "数据持久化": "侧重「ORM vs 原生 SQL 的工程权衡」，用真实的性能/维护场景举例。",
            "IO与NIO": "侧重「阻塞 vs 非阻塞的本质区别」，用连接数/吞吐量场景让读者感受差异。",
            "JVM原理": "侧重「这个 JVM 机制对你的代码行为有什么影响」，不要纯讲规范。",
            "性能优化": "侧重「怎么定位问题」而非「怎么背参数」，给出可操作的排查流程。",
            "微服务架构": "侧重「分布式带来了哪些 Java 单体不存在的问题」，用一致性/可用性场景举例。",
            "网络与Netty": "侧重「为什么 Java 的网络编程需要 Netty」，从 BIO 的痛点引入。",
        }

        parts = [
            f"📌 当前文章动态上下文（请据此调整写作方式）：",
            f"  概念：{concept}  |  分类：{category}  |  难度：{level}",
            "",
            level_hints.get(level, level_hints["进阶"]),
            "",
            category_hints.get(category, ""),
        ]
        return "\n".join(p for p in parts if p)

    dynamic_ctx = _build_dynamic_context()

    publish_guard = (
        "⚠️ 当前为最终发布模式：\n"
        "禁止输出任何 审核/测试/专家评审/结论验证 等内容。\n"
        "只允许输出可直接发布的文章正文。"
    )

    def run_skill(skill_name: str, content: str) -> str:
        system_prompt = _load_skill(skill_dir, skill_name)
        # System 消息放 skill 提示词 + publish_guard + dynamic_ctx（API 可 prefix cache）
        # Human 消息只放本次任务内容，不重复注入上下文
        messages = [
            SystemMessage(content=f"{system_prompt}\n\n{publish_guard}\n\n{dynamic_ctx}"),
            HumanMessage(content=content),
        ]
        return llm.invoke(messages).content

    def java_node(state):
        print("  ▶ Java Expert")
        kg_section = (
            f"{kg_context}\n\n"
            if kg_context else ""
        )
        topic_prompt = (
            f"{kg_section}"
            f"当前文章任务：\n"
            f"  Java概念：{article['java_concept']}\n"
            f"  分类：{article['category']}  难度：{article['level']}\n"
            f"  写作背景：{article['topic']}\n\n"
            f"请以「{article['java_concept']}」为核心，深入解析这个 Java 知识点：\n"
            f"1. 它在真实 Java 项目里解决什么问题？（从一个工程场景出发）\n"
            f"2. Java 为什么这样设计？背后的工程权衡是什么？\n"
            f"3. 核心用法、关键细节、以及 {article['level']} 程度开发者最容易误解的地方\n\n"
            f"注意：请纯粹从 Java 工程师视角写，不要从前端视角切入，前端类比由专门的前端专家处理。"
        )
        result = run_skill("java-expert", topic_prompt)
        _save_step(state["run_dir"], "01_java_expert", result)
        return {"java_output": result}

    def fe_node(state):
        print("  ▶ Frontend Expert")
        result = run_skill("frontend-expert", state["java_output"])
        _save_step(state["run_dir"], "02_frontend_expert", result)
        # 提取契合度评分（供打印和后续使用）
        analogy_score = ""
        for line in result.splitlines()[:5]:
            if "契合度" in line or "Relevance" in line:
                analogy_score = line.strip()
                break
        if analogy_score:
            print(f"     {analogy_score}")
        return {"fe_output": result, "analogy_score": analogy_score}

    def practitioner_node(state):
        print("  ▶ Practitioner")
        result = run_skill("practitioner", state["java_output"])
        _save_step(state["run_dir"], "02b_practitioner", result)
        return {"practitioner_output": result}

    def sysconcept_node(state):
        print("  ▶ SysConcept")
        result = run_skill("sysconcept", state["java_output"])
        _save_step(state["run_dir"], "02c_sysconcept", result)
        # 如果 NOT_APPLICABLE，不注入
        if "NOT_APPLICABLE" in result:
            print("     ⏭ 无系统层内容，跳过")
            return {"sysconcept_output": ""}
        return {"sysconcept_output": result}

    def edu_node(state):
        print("  ▶ Educator")
        analogy_note = (
            f"前端类比契合度评分: {state.get('analogy_score', '未评估')}\n\n"
            if state.get('analogy_score') else ""
        )
        level_note = f"文章难度级别: {article['level']}（请严格按此级别控制内容深度和代码示例数量）\n\n"
        crossref_note = state.get("crossref_hint", "")
        crossref_section = f"{crossref_note}\n\n" if crossref_note else ""
        practitioner_section = (
            f"\n\n---\n## 实战素材（来自 Practitioner，请融入文章）\n\n"
            f"{state.get('practitioner_output', '')}"
            if state.get("practitioner_output") else ""
        )
        sysconcept_section = (
            f"\n\n---\n## 系统层内容（来自 SysConcept，请融入「动手验证」或「实践」节）\n\n"
            f"{state.get('sysconcept_output', '')}"
            if state.get("sysconcept_output") else ""
        )
        merged = (
            level_note + analogy_note + crossref_section
            + state["java_output"] + "\n\n"
            + state["fe_output"]
            + practitioner_section
            + sysconcept_section
        )
        result = run_skill("educator", merged)
        _save_step(state["run_dir"], "03_educator", result)
        return {"edu_output": result}

    def bridge_check_node(state):
        print("  ▶ Bridge Check（前端可读性验收）")
        result = run_skill("bridge-check", state["edu_output"])
        _save_step(state["run_dir"], "03b_bridge", result)
        for line in result.splitlines():
            if "bridge-check:" in line:
                print(f"     🌉 {line.strip()}")
                break
        return {"edu_output": result}

    def visual_node(state):
        print("  ▶ Visual Architect")
        result = run_skill("visual-architect", state["edu_output"])
        _save_step(state["run_dir"], "03b_visual", result)
        return {"edu_output": result}  # 覆盖 edu_output，后续节点使用带图版本

    def diagram_review_node(state):
        print("  ▶ Diagram Reviewer")
        content = state["edu_output"]
        # 如果没有图，直接跳过
        if "<!-- DIAGRAM: NOT_NEEDED -->" in content or "```mermaid" not in content:
            print("     ⏭ 无 Mermaid 图，跳过图审")
            return {}
        result = run_skill("diagram-reviewer", content)
        _save_step(state["run_dir"], "03c_diagram_review", result)
        # 检查是否有修正
        if "DIAGRAM_FIXED" in result:
            print("     🔧 流程图已修正")
        else:
            print("     ✅ 流程图审查通过")
        return {"edu_output": result}

    def fmt_node(state):
        print("  ▶ Formatter")
        result = run_skill("formatter", state["edu_output"])
        _save_step(state["run_dir"], "04_formatter", result)
        return {"fmt_output": result}

    def ops_node(state):
        print("  ▶ Maintainer")
        nav_context = (
            f"当前文章 ID: {article['id']}\n"
            f"当前文章标题: {article['title']}\n"
            f"上一篇标题: {prev_title or '（系列第一篇）'}\n"
            f"下一篇标题: {next_title or '（系列最后一篇）'}\n\n"
            f"以下是文章正文，请根据上述信息生成系列导航和备份区：\n\n"
        )
        result = run_skill("maintainer", nav_context + state["fmt_output"])
        _save_step(state["run_dir"], "05_final_article", result)
        # fmt_output 是干净正文，ops 只提供导航块 + 备份区
        return {"ops_output": result, "final_output": state["fmt_output"]}

    def reviewer_node(state):
        print("  ▶ Reviewer")
        review_input = (
            f"文章标题: {article['title']}\n"
            f"Java概念: {article['java_concept']}\n"
            f"分类: {article['category']}  难度: {article['level']}\n\n"
            f"以下是待评审的文章正文：\n\n"
            f"{state['fmt_output']}"
        )
        result = run_skill("reviewer", review_input)
        _save_step(state["run_dir"], "06_review_critique", result)
        # 提取总分用于打印
        score_line = ""
        for line in result.splitlines():
            if "总分" in line:
                score_line = line.strip()
                break
        if score_line:
            print(f"     📊 {score_line}")
        return {"review_critique": result}

    def revise_node(state):
        print("  ▶ Reviser")
        critique = state.get("review_critique", "")
        if not critique:
            print("     ⏭ 无评审意见，跳过修订")
            return {}
        revise_input = (
            f"## 原文\n\n{state['fmt_output']}\n\n"
            f"## 评审报告\n\n{critique}"
        )
        result = run_skill("reviser", revise_input)
        _save_step(state["run_dir"], "07_revised", result)
        print("     ✅ 修订完成")
        # 修订后的文章替换 final_output
        return {"final_output": result}

    def fact_check_node(state):
        print("  ▶ Fact Checker")
        fc_input = (
            f"文章标题: {article['title']}\n"
            f"Java概念: {article['java_concept']}\n"
            f"分类: {article['category']}  难度: {article['level']}\n\n"
            f"以下是待审计的文章正文：\n\n"
            f"{state['final_output']}"
        )
        result = run_skill("fact-checker", fc_input)
        _save_step(state["run_dir"], "08_fact_check", result)
        # 提取准确性评级用于打印
        for line in result.splitlines():
            if "准确性评级" in line:
                print(f"     🔬 {line.strip()}")
                break
        return {"fact_check": result}

    graph = StateGraph(ArticleState)
    for name, fn in [("java", java_node), ("fe", fe_node),
                     ("practitioner", practitioner_node), ("sysconcept", sysconcept_node),
                     ("edu", edu_node), ("bridge_check", bridge_check_node),
                     ("visual", visual_node),
                     ("diagram_review", diagram_review_node),
                     ("fmt", fmt_node), ("ops", ops_node),
                     ("reviewer", reviewer_node), ("revise", revise_node),
                     ("fact_check", fact_check_node)]:
        graph.add_node(name, fn)
    graph.set_entry_point("java")
    # fe / practitioner / sysconcept 均只依赖 java_output，三者并行执行
    graph.add_edge("java", "fe")
    graph.add_edge("java", "practitioner")
    graph.add_edge("java", "sysconcept")
    # edu 等待三者全部完成后再执行（fan-in）
    graph.add_edge("fe", "edu")
    graph.add_edge("practitioner", "edu")
    graph.add_edge("sysconcept", "edu")
    graph.add_edge("edu", "bridge_check")
    graph.add_edge("bridge_check", "visual")
    graph.add_edge("visual", "diagram_review")
    graph.add_edge("diagram_review", "fmt")
    graph.add_edge("fmt", "ops")
    graph.add_edge("ops", "reviewer")
    graph.add_edge("reviewer", "revise")
    graph.add_edge("revise", "fact_check")
    graph.add_edge("fact_check", END)
    app = graph.compile()

    run_dir = _create_article_dir(article)
    result = app.invoke({"topic": article["title"], "run_dir": run_dir,
                         "crossref_hint": crossref_hint})

    # ── 拆分：正文 = fmt_output，导航+备份 来自 ops_output ──
    article_body = result["final_output"].rstrip()
    ops_raw = result.get("ops_output", "")

    REVIEW_SEP_START = "--- REVIEW_START ---"
    REVIEW_SEP_END   = "--- REVIEW_END ---"

    import re as _re

    # 先提取导航块：从 "---" + "### 系列导航" 开始到 REVIEW_START 之前（或字符串末尾）
    nav_match = _re.search(
        r'(---\s*\n### 系列导航.*?)(?=\n*---\s*REVIEW_START|$)',
        ops_raw, _re.DOTALL
    )
    nav_part = nav_match.group(1).strip() if nav_match else ""

    # 再提取备份区：REVIEW_START … REVIEW_END 之间
    if REVIEW_SEP_START in ops_raw and REVIEW_SEP_END in ops_raw:
        review_part = ops_raw.split(REVIEW_SEP_START, 1)[1].split(REVIEW_SEP_END, 1)[0].strip()
    elif nav_match:
        # REVIEW_START 标记缺失时，把导航块之后的所有内容当备份区，不污染正文
        review_part = ops_raw[nav_match.end():].strip()
    else:
        # 完全解析失败：整体丢进备份区，不让任何内容污染正文
        nav_part    = ""
        review_part = ops_raw.strip()
        print("  ⚠️  ops_node 输出格式异常，导航块未找到，全部写入 review.md")

    # final.md = 文章正文 + 导航
    final_content = article_body
    if nav_part:
        final_content += "\n\n" + nav_part

    final_path = run_dir / "final.md"
    final_path.write_text(final_content, encoding="utf-8")

    if review_part:
        analogy_score = result.get("analogy_score", "")
        if analogy_score:
            review_part = f"## 前端类比质量\n{analogy_score}\n\n" + review_part
        review_path = run_dir / "review.md"
        review_path.write_text(review_part, encoding="utf-8")
        print(f"  📋 备份已分离: {review_path}")

    return final_path


def _save_step(run_dir: Path, name: str, content: str):
    (run_dir / f"{name}.md").write_text(content, encoding="utf-8")


# ─────────────────────────────────────────────
# 主运行逻辑
# ─────────────────────────────────────────────

def run_next(count: int = 1):
    """按大纲顺序生成 count 篇文章。"""
    from outline import OutlineManager
    from knowledge_graph import KnowledgeGraph

    outline = OutlineManager(OUTLINE_PATH, llm)
    if not outline.articles:
        print("❌ 大纲不存在，请先运行：python series_runner.py --outline")
        return

    kg = KnowledgeGraph(KG_PATH, llm)
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
            # 注入知识图谱上下文（只注入与当前概念相关的节点）
            kg_context = kg.get_context_for_concept(article['java_concept'])
            # 前篇引用提示（供 educator 在文章中插入系列引用）
            crossref_hint = kg.get_crossref_hint(article['java_concept'])
            if crossref_hint:
                print(f"  🔗 前篇引用: {crossref_hint.splitlines()[3].strip() if len(crossref_hint.splitlines()) > 3 else '有'}")

            # 获取真实前后篇标题
            all_articles = outline.articles
            idx = next((i for i, a in enumerate(all_articles) if a["id"] == article["id"]), None)
            prev_title = all_articles[idx - 1]["title"] if idx and idx > 0 else ""
            next_title = all_articles[idx + 1]["title"] if idx is not None and idx < len(all_articles) - 1 else ""

            # 生成文章
            final_path = generate_article(article, kg_context, prev_title, next_title, crossref_hint)
            print(f"  ✅ 文章已保存: {final_path}")

            # 更新大纲状态
            outline.mark_done(article["id"], final_path)

            # 增量更新知识图谱
            print("  🧠 更新知识图谱……")
            kg.extract_and_merge(final_path)

            generated += 1

        except Exception as e:
            error_msg = str(e)
            print(f"  ❌ 生成失败: {error_msg}")
            traceback.print_exc()
            outline.mark_error(article["id"], error_msg)

    outline.print_progress()
    print(f"\n本次生成了 {generated} 篇文章。")


# ─────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="系列文章生成器")
    parser.add_argument("--outline", action="store_true", help="生成/查看大纲")
    parser.add_argument("--progress", action="store_true", help="查看生成进度")
    parser.add_argument("--count", type=int, default=1, help="连续生成文章数（默认 1）")
    parser.add_argument("--reset-errors", action="store_true", help="重置所有失败文章")
    parser.add_argument("--force-outline", action="store_true", help="强制重新生成大纲")
    args = parser.parse_args()

    from outline import OutlineManager

    outline = OutlineManager(OUTLINE_PATH, llm)

    if args.outline or args.force_outline:
        outline.generate(force=args.force_outline)
        outline.print_progress()

    elif args.progress:
        outline.print_progress()

    elif args.reset_errors:
        outline.reset_errors()
        outline.print_progress()

    else:
        # 默认：检查大纲是否存在，然后生成文章
        if not outline.articles:
            print("📝 大纲不存在，先自动生成大纲……\n")
            outline.generate()
        run_next(count=args.count)