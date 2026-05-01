"""大纲管理器

生成并持久化《前端工程师系统学 Java》系列文章大纲（约 210 篇），
跟踪每篇文章的生成状态，供 series_runner.py 按序驱动。

路线设计原则：
  - Java 是主体，前端类比是帮助理解的脚手架
  - 按"能最快在真实项目里用上"排序，不是 Java 教科书顺序
  - Spring Boot 实战排在第二，读者第 10 篇就能跑通一个 REST API
  - 每个知识点先讲「Java 中会遇到的问题」，再讲 Java 的解法
  - 前端类比在「Java 解法讲完之后」穿插，不是切入入口

outline.json 结构：
{
  "meta": {
    "series_title": "...",
    "total": 210,
    "generated_at": "...",
    "stats": {"pending": 210, "done": 0, "error": 0}
  },
  "articles": [
    {
      "id": 1,
      "title": "...",
      "topic": "...",       # 给 LLM 的精确指令
      "java_concept": "...",
      "frontend_analogy": "...",
      "category": "...",    # 分类：基础/集合/并发/JVM/设计模式/...
      "level": "...",       # 入门/进阶/高级
      "status": "pending",  # pending / generating / done / error
      "article_path": null,
      "generated_at": null,
      "error_msg": null
    }
  ]
}
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# Pydantic 结构化输出
# ─────────────────────────────────────────────

class ArticleOutlineItem(BaseModel):
    title: str = Field(description="文章标题，20字以内，点明这个Java知识点的核心价值，像老工程师告诉你'这个你必须搞懂'")
    topic: str = Field(description="给写作 Agent 的精确指令：①这个Java知识点在真实项目里解决什么问题（主线，必须放前面）②可选：前端开发者熟悉的类似场景（辅助理解，写在末尾，用括号标注）")
    java_concept: str = Field(description="核心 Java 概念，10字以内")
    frontend_analogy: str = Field(description="对应的前端概念，10字以内，仅供辅助理解")
    category: str = Field(description="分类，从以下选: 基础语法/面向对象/集合框架/异常处理/IO与NIO/并发编程/JVM原理/设计模式/函数式编程/Spring框架/网络与Netty/性能优化/微服务架构")
    level: str = Field(description="难度，从以下选: 入门/进阶/高级")


class SeriesOutline(BaseModel):
    articles: list[ArticleOutlineItem] = Field(description="文章列表")


# ─────────────────────────────────────────────
# 分批生成 Prompt（每批约 25 篇）
# ─────────────────────────────────────────────

BATCH_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """你是《前端工程师系统学 Java》系列文章的策划编辑。

系列定位：
- 读者是有 1-3 年经验的前端工程师，目标是系统学会 Java 后端开发
- **Java 是核心学习对象**：每篇文章出发点是「Java 遇到了什么问题，Java 用什么机制解决」
- **前端类比是事后脚手架**：Java 内容讲完后，才用读者熟悉的 Vue/React/TS 帮助对照，绝不是切入主线
- 学习顺序：能最快在真实项目里用上 → 深入原理 → 扩展架构

写大纲时严格遵守：
- title：点明「这个 Java 知识点的核心价值」，像工程师在 code review 说「这个你必须搞懂」
  ✅ 好：「Java 静态类型：为什么每个变量必须声明类型」「String 为什么是不可变的」
  ❌ 坏：「像 TS 接口一样理解 Java 类型」「用前端视角看 Java 变量」（不能把前端类比放标题）
- topic：给 AI 写作 Agent 的精准指令，必须以 Java 问题/场景为主线，格式：
  「[Java概念]解决了[真实项目里的具体问题]；[level]重点讲[核心要点]。（熟悉[前端概念]的读者可对照理解）」
  ✅ 好：「Java静态类型系统在企业项目中防止运行时类型错误；入门重点讲8种基本类型和声明规则。（熟悉TypeScript类型注解的读者可对照理解）」
  ❌ 坏：「用TypeScript interface类比Java变量声明」（前端优先，完全错误）
- 同一分类内文章有递进关系，难度平稳上升
- 避免重复已有文章
""",
    ),
    (
        "human",
        """请为「{category}」分类生成 {count} 篇文章大纲。

{existing_hint}

要求：
- 覆盖 {category} 从 {level_range} 的完整知识点
- 每篇聚焦一个具体的 Java 知识点，不要太宽泛
- title：不能用「像XX一样」的前端类比句式，要直接点明 Java 知识点价值
- topic：以「Java 概念 + 在真实项目里解决什么问题」为主线；前端类比仅作括号内的可选参考写在最末
- 难度梯度要平滑，不要跳跃
""",
    ),
])


# ─────────────────────────────────────────────
# 大纲管理器
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# 系列分类规划
# 顺序 = 前端工程师实战学习路线（能最快上手做事 → 再深入原理）
# ─────────────────────────────────────────────
SERIES_PLAN = [
    # ── 阶段一：快速上手（先跑起来，再理解为什么）──────────────
    ("Java快速上手",     8, "入门",
     "类比 JS/TS 快速掌握：静态类型 / 方法 / 控制流 / 面向对象语法"),

    # ── 阶段二：立刻上手做后端（第 10 篇就能跑通 REST API）─────
    ("Spring Boot实战", 22, "入门到进阶",
     "REST API / 依赖注入 / 配置管理 / 过滤器 / 全局异常，前端最快见效的模块"),

    # ── 阶段三：在 Spring 项目里理解 OOP ──────────────────────
    ("面向对象设计",    15, "入门到进阶",
     "在 Spring 项目中理解类 / 接口 / 继承 / 多态 / 泛型，对比 TS 接口语义"),

    # ── 阶段四：函数式编程（前端最熟悉，上手最快）────────────
    ("函数式编程",      12, "进阶",
     "Lambda / Stream / Optional / 方法引用，类比 JS Array.map/filter/reduce"),

    # ── 阶段五：数据持久化（做项目必须的）────────────────────
    ("数据持久化",      15, "进阶",
     "JPA / MyBatis / 事务，类比前端 fetch + Pinia/Zustand 状态管理"),

    # ── 阶段六：错误处理（做了 Spring 之后才需要深入）────────
    ("异常处理",         8, "入门到进阶",
     "CheckedException / RuntimeException / 全局异常处理，对比前端 error boundary"),

    # ── 阶段七：集合框架（工程高频，深入理解后更高效）────────
    ("集合框架",        15, "进阶",
     "List / Map / Set / Queue 到迭代器设计，与 JS 数据结构的异同"),

    # ── 阶段八：并发编程（Java 核心竞争力）────────────────────
    ("并发编程",        25, "进阶到高级",
     "从 Promise 类比到 Thread / 线程池 / CompletableFuture / JUC 全体系"),

    # ── 阶段九：设计模式（代码可维护性的关键）────────────────
    ("设计模式",        22, "进阶到高级",
     "GoF 23 种模式在 Spring / 业务代码中的实际应用，对比前端 composables 模式"),

    # ── 阶段十：IO 与文件（Node.js 开发者的已知领域）────────
    ("IO与文件",        10, "进阶",
     "从 Node.js fs / stream 到 Java IO / NIO，同步流与响应式流对比"),

    # ── 阶段十一：JVM 原理（进阶，线上问题必备）─────────────
    ("JVM原理",         15, "高级",
     "内存模型 / GC 算法 / 类加载 / 字节码，读懂线上 OOM 和 GC 日志"),

    # ── 阶段十二：性能优化（生产环境调优实战）────────────────
    ("性能优化",        10, "高级",
     "从 profiling 到 JIT / 对象池 / 缓存优化，实战调优案例"),

    # ── 阶段十三：微服务架构（企业级实战）────────────────────
    ("微服务架构",      18, "高级",
     "从单体到 Spring Cloud / Service Mesh / Cloud Native，前端视角的全栈视野"),

    # ── 阶段十四：网络与 Netty（深水区）──────────────────────
    ("网络与Netty",     15, "高级",
     "从 WebSocket 到 Netty 事件循环 / Reactor 模式，类比浏览器 EventLoop"),
]


class OutlineManager:
    """大纲管理器：生成、持久化、状态跟踪。"""

    def __init__(self, outline_path: str | Path, llm: ChatOpenAI):
        self.outline_path = Path(outline_path)
        self.llm = llm
        self._data = self._load()

    # ── 持久化 ──────────────────────────────────

    def _load(self) -> dict:
        if self.outline_path.exists():
            return json.loads(self.outline_path.read_text(encoding="utf-8"))
        return {"meta": {}, "articles": []}

    def save(self):
        self.outline_path.parent.mkdir(parents=True, exist_ok=True)
        self._data["meta"]["stats"] = self._calc_stats()
        self.outline_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _calc_stats(self) -> dict:
        articles = self._data["articles"]
        from collections import Counter
        c = Counter(a["status"] for a in articles)
        return {
            "total": len(articles),
            "pending": c.get("pending", 0),
            "generating": c.get("generating", 0),
            "done": c.get("done", 0),
            "error": c.get("error", 0),
        }

    # ── 生成大纲 ─────────────────────────────────

    def generate(self, force: bool = False) -> "OutlineManager":
        """生成完整系列大纲（如已存在则跳过，force=True 强制重生成）。"""
        if self._data["articles"] and not force:
            stats = self._calc_stats()
            print(f"📋 大纲已存在：{stats['total']} 篇（done={stats['done']}, pending={stats['pending']}）")
            return self

        print("📝 正在生成系列大纲（每批 5 篇，逐批调用）……")

        structured_llm = self.llm.model_copy(
            update={"extra_body": {"enable_thinking": False}}
        ).with_structured_output(
            SeriesOutline, method="function_calling"
        )
        chain = BATCH_PROMPT | structured_llm

        all_articles = []
        article_id = 1
        BATCH_SIZE = 5  # 每次最多 5 篇，避免 qwen 函数调用超限

        for category, count, level_range, level_desc in SERIES_PLAN:
            print(f"  [{category}] 目标 {count} 篇", end="", flush=True)
            cat_articles = []
            remaining = count

            while remaining > 0:
                batch = min(BATCH_SIZE, remaining)
                existing_titles = [
                    a["title"] if isinstance(a, dict) else a.title
                    for a in (all_articles + cat_articles)[-5:]
                ]
                existing_hint = (
                    f"前序文章（请勿重复）：{'、'.join(existing_titles)}"
                    if existing_titles else ""
                )
                try:
                    result: SeriesOutline = chain.invoke({
                        "category": category,
                        "count": batch,
                        "level_range": level_desc,
                        "existing_hint": existing_hint,
                    })
                    cat_articles.extend(result.articles)
                    remaining -= len(result.articles)
                    print(f" ·{len(result.articles)}", end="", flush=True)
                except Exception as e:
                    print(f" ✗({e})", end="", flush=True)
                    remaining -= batch  # 跳过失败批次，继续

            for item in cat_articles:
                all_articles.append({
                    "id": article_id,
                    "title": item.title,
                    "topic": item.topic,
                    "java_concept": item.java_concept,
                    "frontend_analogy": item.frontend_analogy,
                    "category": item.category or category,
                    "level": item.level,
                    "status": "pending",
                    "article_path": None,
                    "generated_at": None,
                    "error_msg": None,
                })
                article_id += 1
            print(f"  → 实际 {len(cat_articles)} 篇 ✓")

        self._data = {
            "meta": {
                "series_title": "前端工程师系统学 Java",
                "generated_at": datetime.now().isoformat(),
            },
            "articles": all_articles,
        }
        self.save()
        stats = self._calc_stats()
        print(f"\n✅ 大纲生成完毕：共 {stats['total']} 篇文章")
        return self

    def append_category(self, category: str, count: int, level_desc: str) -> "OutlineManager":
        """向已有大纲追加一个分类的文章（用于补充失败的分类）。"""
        structured_llm = self.llm.model_copy(
            update={"extra_body": {"enable_thinking": False}}
        ).with_structured_output(
            SeriesOutline, method="function_calling"
        )
        chain = BATCH_PROMPT | structured_llm
        BATCH_SIZE = 5

        existing_ids = {a["id"] for a in self._data["articles"]}
        next_id = max(existing_ids, default=0) + 1
        cat_articles = []
        remaining = count

        print(f"  [{category}] 补充 {count} 篇", end="", flush=True)
        while remaining > 0:
            batch = min(BATCH_SIZE, remaining)
            existing_titles = (
                [a["title"] for a in self._data["articles"][-5:]]
                + [a.title if hasattr(a, "title") else a["title"] for a in cat_articles[-5:]]
            )
            existing_hint = (
                f"前序文章（请勿重复）：{'、'.join(t if isinstance(t, str) else t['title'] for t in existing_titles[-5:])}"
                if existing_titles else ""
            )
            try:
                result: SeriesOutline = chain.invoke({
                    "category": category,
                    "count": batch,
                    "level_range": level_desc,
                    "existing_hint": existing_hint,
                })
                cat_articles.extend(result.articles)
                remaining -= len(result.articles)
                print(f" ·{len(result.articles)}", end="", flush=True)
            except Exception as e:
                print(f" ✗({e})", end="", flush=True)
                remaining -= batch

        for item in cat_articles:
            self._data["articles"].append({
                "id": next_id,
                "title": item.title,
                "topic": item.topic,
                "java_concept": item.java_concept,
                "frontend_analogy": item.frontend_analogy,
                "category": item.category or category,
                "level": item.level,
                "status": "pending",
                "article_path": None,
                "generated_at": None,
                "error_msg": None,
            })
            next_id += 1
        self.save()
        print(f"  → 实际 {len(cat_articles)} 篇 ✓")
        return self



    def next_pending(self) -> Optional[dict]:
        """获取下一篇待生成的文章。"""
        for a in self._data["articles"]:
            if a["status"] == "pending":
                return a
        return None

    def mark_generating(self, article_id: int):
        self._update(article_id, {"status": "generating"})

    def mark_done(self, article_id: int, article_path: str):
        self._update(article_id, {
            "status": "done",
            "article_path": str(article_path),
            "generated_at": datetime.now().isoformat(),
            "error_msg": None,
        })

    def mark_error(self, article_id: int, error_msg: str):
        self._update(article_id, {
            "status": "error",
            "error_msg": error_msg[:200],
            "generated_at": datetime.now().isoformat(),
        })

    def reset_errors(self):
        """将所有 error 状态重置为 pending，以便重试。"""
        for a in self._data["articles"]:
            if a["status"] == "error":
                a["status"] = "pending"
                a["error_msg"] = None
        self.save()
        print("↩️  已重置所有失败文章为待生成")

    def _update(self, article_id: int, fields: dict):
        for a in self._data["articles"]:
            if a["id"] == article_id:
                a.update(fields)
                break
        self.save()

    # ── 查询接口 ─────────────────────────────────

    def get_article(self, article_id: int) -> Optional[dict]:
        for a in self._data["articles"]:
            if a["id"] == article_id:
                return a
        return None

    def list_articles(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
        level: Optional[str] = None,
    ) -> list[dict]:
        result = self._data["articles"]
        if status:
            result = [a for a in result if a["status"] == status]
        if category:
            result = [a for a in result if a["category"] == category]
        if level:
            result = [a for a in result if a["level"] == level]
        return result

    def print_progress(self):
        stats = self._calc_stats()
        total = stats["total"]
        done = stats["done"]
        pct = done / total * 100 if total else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"\n📋 系列进度 [{bar}] {done}/{total} ({pct:.1f}%)")
        print(f"   pending={stats['pending']}  generating={stats['generating']}  "
              f"done={stats['done']}  error={stats['error']}")

        # 按分类统计
        from collections import defaultdict
        cat_stats: dict[str, dict] = defaultdict(lambda: {"done": 0, "total": 0})
        for a in self._data["articles"]:
            cat_stats[a["category"]]["total"] += 1
            if a["status"] == "done":
                cat_stats[a["category"]]["done"] += 1
        print()
        for cat, s in sorted(cat_stats.items()):
            bar2 = "█" * s["done"] + "░" * (s["total"] - s["done"])
            print(f"   {cat:<12} [{bar2}] {s['done']}/{s['total']}")

    @property
    def articles(self) -> list[dict]:
        return self._data["articles"]

    @property
    def meta(self) -> dict:
        return self._data.get("meta", {})
