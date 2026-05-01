"""知识图谱模块

从 Markdown 技术文章中提取知识节点和关系，持久化为 JSON，
供 Agent 调用做上下文回灌，以及历史查看。

使用 OpenAI function_calling 结构化输出，兼容 DashScope 等国内模型。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# Pydantic 数据模型（供 function_calling 结构化输出）
# ─────────────────────────────────────────────

class KGNode(BaseModel):
    name: str = Field(description="概念或实体的名称，简洁唯一")
    type: str = Field(description="类型，从以下选择: concept/technology/pattern/principle/tool/analogy")
    description: str = Field(description="一句话描述这个节点的核心含义")


class KGEdge(BaseModel):
    source: str = Field(description="源节点名称，必须与 nodes 中某个 name 一致")
    target: str = Field(description="目标节点名称，必须与 nodes 中某个 name 一致")
    relation: str = Field(description="关系标签，如: 实现/类比/包含/依赖/扩展/对应/触发")
    description: str = Field(description="一句话描述这条关系")


class KGExtractionResult(BaseModel):
    summary: str = Field(description="本文核心主题的一句话总结（20字以内）")
    nodes: list[KGNode] = Field(description="提取的知识节点列表，10-25个")
    edges: list[KGEdge] = Field(description="节点间的关系列表，15-35条")


# ─────────────────────────────────────────────
# 知识图谱管理器
# ─────────────────────────────────────────────

EXTRACT_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """你是一个技术知识图谱提取专家，专注于《Java 设计思想 → 前端认知映射》系列文章。

从文章中提取：
1. **核心技术概念**（Java/前端均可）
2. **设计模式和原则**
3. **两端之间的类比关系**（这是最重要的边）
4. **概念之间的依赖/包含/扩展关系**

注意：
- 节点名称要简洁（4-12字），类型从 concept/technology/pattern/principle/tool/analogy 中选
- 关系标签要精准，优先使用: 类比/实现/包含/依赖/扩展/对应/触发
- edges 的 source/target 必须与 nodes 中的 name 完全对应
""",
    ),
    ("human", "请从以下文章中提取知识图谱：\n\n{text}"),
])


class KnowledgeGraph:
    """持久化知识图谱，支持增量合并和 Agent 调用。

    图谱文件结构（graph.json）：
    {
      "nodes": {"节点名": {"name", "type", "description", "sources": [...]}},
      "edges": [{"source", "target", "relation", "description", "source_article"}],
      "articles": [{"id", "path", "title", "summary", "processed_at", "nodes_added", "edges_added"}]
    }
    """

    def __init__(self, graph_path: str | Path, llm: ChatOpenAI):
        self.graph_path = Path(graph_path)
        self.llm = llm
        self._data = self._load()

    # ── 持久化 ──────────────────────────────────

    def _load(self) -> dict:
        if self.graph_path.exists():
            return json.loads(self.graph_path.read_text(encoding="utf-8"))
        return {"nodes": {}, "edges": [], "articles": []}

    def save(self):
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)
        self.graph_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 提取 & 合并 ──────────────────────────────

    def extract_and_merge(self, md_path: str | Path, force: bool = False) -> dict:
        """从 Markdown 文章提取知识图谱并增量合并到全局图谱。

        Args:
            md_path: 文章路径
            force:   True 则强制重新处理（即使已处理过）

        Returns:
            合并后的完整图谱 dict
        """
        md_path = Path(md_path)
        article_id = md_path.parent.name  # e.g. "001_Java"

        # 检查是否已处理
        if not force:
            processed_ids = [a["id"] for a in self._data["articles"]]
            if article_id in processed_ids:
                print(f"⏭  已处理过: {article_id}，跳过（使用 force=True 可强制重跑）")
                return self._data

        text = md_path.read_text(encoding="utf-8")
        # 取文章标题（第一行 # 开头）
        title = next(
            (l.lstrip("#").strip() for l in text.splitlines() if l.startswith("#")),
            article_id,
        )

        print(f"🔍  提取中: {article_id} — {title[:30]}")
        result = self._extract(text)
        print(f"   → 节点 {len(result.nodes)} 个，关系 {len(result.edges)} 条")

        # 合并节点（按名称去重）
        new_nodes = 0
        for node in result.nodes:
            if node.name not in self._data["nodes"]:
                self._data["nodes"][node.name] = {
                    "name": node.name,
                    "type": node.type,
                    "description": node.description,
                    "sources": [article_id],
                }
                new_nodes += 1
            else:
                if article_id not in self._data["nodes"][node.name]["sources"]:
                    self._data["nodes"][node.name]["sources"].append(article_id)

        # 合并边（按 source+target+relation 三元组去重）
        existing = {
            (e["source"], e["target"], e["relation"]) for e in self._data["edges"]
        }
        new_edges = 0
        for edge in result.edges:
            key = (edge.source, edge.target, edge.relation)
            if key not in existing:
                self._data["edges"].append(
                    {
                        "source": edge.source,
                        "target": edge.target,
                        "relation": edge.relation,
                        "description": edge.description,
                        "source_article": article_id,
                    }
                )
                existing.add(key)
                new_edges += 1

        # 记录文章（如已存在则更新）
        self._data["articles"] = [
            a for a in self._data["articles"] if a["id"] != article_id
        ]
        self._data["articles"].append(
            {
                "id": article_id,
                "path": str(md_path),
                "title": title[:60],
                "summary": result.summary,
                "processed_at": datetime.now().isoformat(),
                "nodes_added": new_nodes,
                "edges_added": new_edges,
            }
        )

        self.save()
        print(f"   ✅ 新增节点 {new_nodes}，新增关系 {new_edges}，已保存")
        return self._data

    def _extract(self, text: str) -> KGExtractionResult:
        """调用 LLM 提取，使用 function_calling 兼容 DashScope。

        注意：bind(extra_body=...) 无效——with_structured_output 通过 __getattr__
        委托到原始 LLM，bound kwargs 被绕过。必须用 model_copy 在实例级别设置
        extra_body，才能让 DashScope 关闭 thinking mode（thinking mode 下
        tool_choice=required 会报 400）。
        """
        llm_no_thinking = self.llm.model_copy(
            update={"extra_body": {"enable_thinking": False}}
        )
        structured_llm = llm_no_thinking.with_structured_output(
            KGExtractionResult, method="function_calling"
        )
        chain = EXTRACT_PROMPT | structured_llm
        return chain.invoke({"text": text[:8000]})

    # ── Agent 调用接口 ────────────────────────────

    def get_context_for_concept(self, java_concept: str, max_nodes: int = 20) -> str:
        """为指定 Java 概念做关键词打分 + 一跳邻居扩展，只返回相关节点的上下文。

        策略：
        1. 将 java_concept 拆成关键词，在节点名（权重×3）和描述（权重×1）中打分
        2. 对命中的种子节点，通过 edges 扩展一跳邻居（权重×0.4），避免孤立片段
        3. 无任何命中时退化到最近 5 篇文章的节点
        """
        if not self._data["nodes"]:
            return ""

        # 拆关键词（按空格/斜杠/顿号切分，保留 ≥2 字的词）
        concept_clean = java_concept.replace("/", " ").replace("、", " ") \
                                    .replace("（", " ").replace("）", " ")
        keywords = [kw for kw in concept_clean.split() if len(kw) >= 2] or [java_concept]

        # 节点打分
        node_scores: dict[str, float] = {}
        for name, node in self._data["nodes"].items():
            score = 0.0
            for kw in keywords:
                if kw in node["name"]:
                    score += 3.0
                elif kw in node["description"]:
                    score += 1.0
            # 单汉字部分匹配（补充中文词边界）
            for ch in java_concept:
                if "\u4e00" <= ch <= "\u9fff" and ch in node["name"]:
                    score += 0.3
            if score > 0:
                node_scores[name] = score

        # 一跳邻居扩展
        seed_names = set(node_scores)
        for edge in self._data["edges"]:
            s, t = edge["source"], edge["target"]
            if s in seed_names and t not in node_scores:
                node_scores[t] = node_scores[s] * 0.4
            elif t in seed_names and s not in node_scores:
                node_scores[s] = node_scores[t] * 0.4

        # 排序取 top N
        top_names = sorted(node_scores, key=lambda x: -node_scores[x])[:max_nodes]
        top_nodes = [self._data["nodes"][n] for n in top_names if n in self._data["nodes"]]
        top_name_set = set(top_names)

        # 无命中时退化到最近 5 篇文章的节点
        if not top_nodes:
            recent_ids = {a["id"] for a in self._data["articles"][-5:]}
            for node in self._data["nodes"].values():
                if any(s in recent_ids for s in node.get("sources", [])):
                    top_nodes.append(node)
                    top_name_set.add(node["name"])
                    if len(top_nodes) >= max_nodes:
                        break
            if not top_nodes:
                return ""

        # 两端都在 top 内的边
        relevant_edges = [
            e for e in self._data["edges"]
            if e["source"] in top_name_set and e["target"] in top_name_set
        ][:20]

        articles = self._data["articles"]
        lines = [
            f"🧠 前篇知识积累（已生成 {len(articles)} 篇）— 检索到与「{java_concept}」相关的 {len(top_nodes)} 个节点",
            "写作时请勿重复解释下列已覆盖的概念，必要时可直接「如前篇所述」。",
            "",
            "【相关知识节点】",
        ]
        by_type: dict[str, list[str]] = {}
        for n in top_nodes:
            by_type.setdefault(n["type"], []).append(n["name"])
        for t, names in sorted(by_type.items()):
            lines.append(f"  [{t}] {' / '.join(names)}")

        if relevant_edges:
            lines.append("\n【相关已建立的关系（可在文章中引用或延伸）】")
            for edge in relevant_edges:
                lines.append(f"  {edge['source']} —{edge['relation']}→ {edge['target']}")

        return "\n".join(lines)

    def get_crossref_hint(self, java_concept: str, max_refs: int = 10) -> str:
        """返回「概念 → 已覆盖它的前篇文章」映射，供 educator 插入引用。

        格式示例：
            【前篇引用提示】
            写作时，如果文章中自然提到下列概念，请在第一次出现时加括号引用，
            例如：「（参见第1篇《Java 静态类型》）」
            - 「静态类型」「编译期检查」→ 第1篇《Java 静态类型：为什么...》
            - 「for-each」「迭代器」→ 第3篇《Java 控制流》

        注意：只在概念自然出现时引用，不要为了引用而强行插入。
        """
        if not self._data["nodes"] or not self._data["articles"]:
            return ""

        # 构建 article_id → {num, title} 的查找表
        article_map: dict[str, dict] = {}
        for a in self._data["articles"]:
            # 从 id（如 "003_控制流"）提取序号
            num_str = a["id"].split("_")[0].lstrip("0") or "0"
            article_map[a["id"]] = {"num": num_str, "title": a["title"]}

        # 用相同的关键词打分逻辑找相关节点
        concept_clean = java_concept.replace("/", " ").replace("、", " ") \
                                    .replace("（", " ").replace("）", " ")
        keywords = [kw for kw in concept_clean.split() if len(kw) >= 2] or [java_concept]

        node_scores: dict[str, float] = {}
        for name, node in self._data["nodes"].items():
            score = 0.0
            for kw in keywords:
                if kw in node["name"]:
                    score += 3.0
                elif kw in node["description"]:
                    score += 1.0
            for ch in java_concept:
                if "\u4e00" <= ch <= "\u9fff" and ch in node["name"]:
                    score += 0.3
            if score > 0:
                node_scores[name] = score

        # 一跳扩展
        seed_names = set(node_scores)
        for edge in self._data["edges"]:
            s, t = edge["source"], edge["target"]
            if s in seed_names and t not in node_scores:
                node_scores[t] = node_scores[s] * 0.4
            elif t in seed_names and s not in node_scores:
                node_scores[s] = node_scores[t] * 0.4

        # 按文章分组：article_id → [节点名列表]
        article_concepts: dict[str, list[str]] = {}
        for name in sorted(node_scores, key=lambda x: -node_scores[x])[:30]:
            node = self._data["nodes"].get(name)
            if not node:
                continue
            for src in node.get("sources", []):
                if src in article_map:
                    article_concepts.setdefault(src, []).append(name)

        if not article_concepts:
            return ""

        # 按文章序号排序
        sorted_articles = sorted(
            article_concepts.items(),
            key=lambda x: int(article_map[x[0]]["num"] or 0)
        )[:max_refs]

        lines = [
            "【前篇引用提示】",
            "写作时，如果文章中自然提到下列概念，请在该概念第一次出现时加一个括号引用。",
            "格式：「（参见第X篇《标题》）」。只在概念自然出现时引用，不要强行插入。",
            "",
        ]
        for art_id, concepts in sorted_articles:
            info = article_map[art_id]
            concept_str = "」「".join(concepts[:5])  # 最多列5个概念
            # 标题截短至 25 字
            short_title = info["title"][:25] + ("…" if len(info["title"]) > 25 else "")
            lines.append(f"  - 「{concept_str}」→ 第{info['num']}篇《{short_title}》")

        return "\n".join(lines)

    def get_context_for_agent(self, max_nodes: int = 60) -> str:
        """返回全量图谱概览（用于调试/统计，不注入文章生成 prompt）。"""
        if not self._data["nodes"]:
            return ""

        articles = self._data["articles"]
        nodes = list(self._data["nodes"].values())
        lines = [
            f"🧠 知识图谱：{len(articles)} 篇文章 | {len(nodes)} 个节点 | {len(self._data['edges'])} 条关系",
            "",
            "【已覆盖文章】",
        ]
        for a in articles:
            lines.append(f"  • {a['id']}：{a.get('summary', a['title'])}")

        lines.append("\n【知识节点（按类型）】")
        by_type: dict[str, list[str]] = {}
        for n in nodes[:max_nodes]:
            by_type.setdefault(n["type"], []).append(n["name"])
        for t, names in sorted(by_type.items()):
            lines.append(f"  [{t}] {' / '.join(names)}")

        # 核心关系（前 30 条）
        lines.append("\n【核心关系】")
        for edge in self._data["edges"][:30]:
            lines.append(
                f"  {edge['source']} —{edge['relation']}→ {edge['target']}"
            )

        return "\n".join(lines)

    # ── 查询接口 ─────────────────────────────────

    def search_nodes(self, query: str) -> list[dict]:
        """关键词搜索节点（名称 + 描述）。"""
        q = query.lower()
        return [
            n
            for n in self._data["nodes"].values()
            if q in n["name"].lower() or q in n["description"].lower()
        ]

    def get_history(self) -> list[dict]:
        """返回已处理文章历史列表。"""
        return self._data["articles"]

    def get_node(self, name: str) -> Optional[dict]:
        """按名称获取节点及其关联边。"""
        node = self._data["nodes"].get(name)
        if not node:
            return None
        related_edges = [
            e
            for e in self._data["edges"]
            if e["source"] == name or e["target"] == name
        ]
        return {**node, "edges": related_edges}

    @property
    def stats(self) -> dict:
        return {
            "articles": len(self._data["articles"]),
            "nodes": len(self._data["nodes"]),
            "edges": len(self._data["edges"]),
        }

    def print_stats(self):
        s = self.stats
        print(
            f"📊 图谱统计：{s['articles']} 篇文章 | {s['nodes']} 个节点 | {s['edges']} 条关系"
        )


# ─────────────────────────────────────────────
# 便捷函数：批量处理 outputs/ 下所有文章
# ─────────────────────────────────────────────

def build_graph_from_outputs(
    outputs_dir: str | Path,
    graph_path: str | Path,
    llm: ChatOpenAI,
    force: bool = False,
) -> KnowledgeGraph:
    """扫描 outputs/ 目录，对每个子目录下的 final.md 提取并合并知识图谱。

    Args:
        outputs_dir: outputs 目录路径
        graph_path:  图谱 JSON 保存路径
        llm:         LLM 实例
        force:       是否强制重新处理所有文章

    Returns:
        KnowledgeGraph 实例
    """
    outputs_dir = Path(outputs_dir)
    kg = KnowledgeGraph(graph_path, llm)

    articles = sorted(
        [
            p / "final.md"
            for p in outputs_dir.iterdir()
            if p.is_dir() and p.name[0].isdigit() and (p / "final.md").exists()
        ]
    )

    if not articles:
        print("⚠️  未找到任何 final.md 文件")
        return kg

    print(f"📂 找到 {len(articles)} 篇文章")
    for md_path in articles:
        kg.extract_and_merge(md_path, force=force)

    kg.print_stats()
    return kg
