<!-- 控制性问题：在多人协作的 Java 项目中，继承到底该用来建立类型契约，还是用来偷懒写代码？ -->

架构组定义了 `BaseService` 统一处理分页和异常，业务线直接 `extends`（继承关键字，用于建立父子类关系）提速。中期父类膨胀改了一行逻辑，下游集成测试全挂，排查耗时数天。**继承的本质不是少敲键盘，而是用编译期约束锁定核心流程，用 `is-a`（“是一种”的语义关系）语义收敛跨团队交互范式。**

做大型 Spring Boot 项目时，你一定会遇到这种“前期爽、后期崩”的继承树。很多团队把继承当成工具类的搬运工，结果父类塞满日志、脱敏、灰度逻辑，变成臃肿的上帝类。**记住这个锚点：强制边界，编译器替你兜底。** 真正的继承只传递两样东西：稳定的算法骨架和严格的类型契约。

Java 设计者经过权衡，选择了单继承加接口的折中方案。其底层哲学很明确：可维护性永远高于灵活性。当你写下继承语句时，你不仅在复用代码，更是在声明一种强烈的语义绑定。为了遏制滥用，Java 提供了三把锁：`final`（修饰方法表示不可重写，修饰类表示不可继承）、`protected`（可见范围仅限同包及子类）、以及构造函数的隐式调用链。

```java
public abstract class BaseQueryService<T> {
    protected abstract List<String> buildBaseConditions();
    public final PageResult<T> executeQuery(PageParam param) {
        validateParam(param);
        List<String> conditions = buildBaseConditions();
        return doDbQuery(param, conditions);
    }
    private void validateParam(PageParam param) { /* 统一拦截非法参数 */ }
    protected abstract PageResult<T> doDbQuery(PageParam param, List<String> conditions);
}
```

看到 `final` 锁死 `executeQuery` 了吗？**强制边界，编译器替你兜底**，核心校验和结果包装的流程绝对不允许被下游业务线篡改。`abstract`（抽象方法，没有方法体，强制子类实现）标记的方法构成了清晰的扩展点。调用方只需面向父类编程，即可享受统一规范，同时获得业务定制能力。子类能完全替代父类使用，且调用方不需要修改任何调度代码。

**📐 继承结构示意：模板方法与类型契约的落地形态**
```mermaid
classDiagram
  class A["基础查询服务"] {
    <<abstract>>
    构建基础条件()
    执行数据库查询()
    执行查询()
  }
  class B["业务实现子类"] {
    构建基础条件()
    执行数据库查询()
  }
  A <|-- B
  note right of A : final锁死流程<br/>abstract提供扩展点
```

这就引出一个高频踩坑点：`protected` 的真实作用域。它允许不同包的子类访问，但很多开发者把它误当作公开 API。一旦下游团队直接调用父类的 `protected` 方法，封装性瞬间破产，父类的内部实现细节就会像黑洞一样吞噬下游的测试用例。

> 🔍 精确说明：`protected` 不是“半公开”，它是给子类留的后门。若其他模块确实需要调用某段逻辑，应将其提升为 `public` 或拆分为独立的工具类，保持父类的纯粹性。

如果你熟悉 Vue 或 React，这套架构思想其实已经迁移到了现代前端工程中。前端虽然淘汰了类继承，但 Vue 的 Composables 或 React 的自定义 Hook 完美复刻了“模板方法 + 类型契约”的协作模式。

```typescript
// Vue Composables 示例
export function useBaseQuery<T>(param: PageParam) {
  const execute = () => {
    validateParam(param) // 🔒 类似 final 锁死流程
    const conditions = buildConditions() // 🔓 类似 protected abstract 强制实现
    data.value = doDbQuery(param, conditions)
  }
  return { execute }
}
```

两者的工程目标完全一致：用结构约束换取大型项目的协作稳定性。区别在于载体，Java 靠类层级和 JVM 运行时动态分派，前端靠 TypeScript 编译期类型推断和纯函数闭包。这里必须划清界限：Java 继承会传递实例状态和构造顺序，而前端的 Hook 每次调用都会创建独立的响应式变量。不要把前端的响应式变量等同于 Java 的成员变量去跨组件共享，否则会导致数据污染和难以追踪的状态竞争。

理解了机制，再看工程决策就清晰了。继承是一把双刃剑。它得到了多态调度和统一的代码骨架，但付出了强耦合和测试复杂度上升的代价。当父子之间存在明确的 `is-a` 关系，且父类提供极少变更的骨架时，放心用。但如果只是为了复用几个常量，或者父子关系会随着业务迭代频繁变动，立刻停止。特别是当需要横向切面能力（如事务、缓存）时，强行塞入继承树只会导致类爆炸。这时候该用组合模式（把另一个类的对象作为字段持有，而非继承它），配合 Spring AOP 解决正交需求。

> 💡 记忆回扣：**强制边界，编译器替你兜底**。判断标准很简单：写下继承语句前问自己，“如果父类删掉这个方法，子类还能独立存在并保持业务语义正确吗？”如果不能，你们的关系是依赖，不是继承。

落地到日常开发，建议开启 IDE 的 `MissingOverride` 检查，所有重写方法必须显式标注 `@Override`（重写注解，告知编译器检查签名一致性）。在静态扫描工具中配置规则：继承链深度不超过 3 层，禁止在父类编写具体业务分支。当发现子类开始大量覆盖父类非抽象方法时，不要硬扛，立即重构为接口契约加独立 Service 注入。

大型项目的协作摩擦，往往始于对继承关系的随意使用。把继承留给类型契约，把逻辑复用交给组合与依赖注入。你的代码树才会越搭越稳，而不是越改越乱。

---

### 系列导航

**上一篇**：[Java接口是解耦依赖的强制契约](#)
**下一篇**：[Java多态是Spring自动注入的底层机制](#)

> 这是「前端工程师系统学 Java」系列第 33 篇，系统解读 Java 设计哲学（面向前端工程师）。
