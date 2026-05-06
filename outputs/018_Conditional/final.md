<!-- 控制性问题：Spring 的 @Conditional 如何让 Bean 在启动时根据运行时条件决定是否注册？ -->

你在做微服务项目时，一定遇到过这种场景：项目同时支持 Kafka 和 RabbitMQ，但生产环境只部署了 Kafka。如果你无脑注册 RabbitMQ 的 Bean，启动时就会因为缺少连接工厂而直接崩溃。传统做法是用 `@Profile` 按环境名切换，或者写一堆 `if-else` 判断类路径是否存在——但前者太死板（只能按 profile 名），后者把条件逻辑散落在代码里，维护起来像在垃圾堆里找钥匙。

**Spring 的 `@Conditional` 机制解决了这个问题：它把“这个 Bean 该不该注册”的决策，从编译期硬编码延迟到容器启动时的运行时判断，通过声明式注解 + 可扩展的 `Condition` 接口，让 Bean 的注册完全由外部条件（类路径、属性、系统环境、自定义逻辑）控制。**

---

## 先看一个最直接的例子

假设你有一个配置类，想根据应用属性 `use.memory.db` 来决定是用 H2 内存数据库还是 MySQL 生产数据库：

```java
@Configuration
public class DataSourceConfig {

    @Bean
    @Conditional(OnMemoryDbCondition.class)
    public DataSource memoryDataSource() {
        return new EmbeddedDatabaseBuilder()
                .setType(EmbeddedDatabaseType.H2)
                .build();
    }

    @Bean
    @Conditional(OnMissingClassCondition.class) // 另一个条件：没有H2依赖时用MySQL
    public DataSource productionDataSource() {
        HikariDataSource ds = new HikariDataSource();
        ds.setJdbcUrl("jdbc:mysql://localhost:3306/mydb");
        return ds;
    }
}
```

其中 `OnMemoryDbCondition` 是你自己实现的 `Condition` 接口：

```java
public class OnMemoryDbCondition implements Condition {
    @Override
    public boolean matches(ConditionContext context, AnnotatedTypeMetadata metadata) {
        String useMemoryDb = context.getEnvironment().getProperty("use.memory.db");
        return "true".equalsIgnoreCase(useMemoryDb);
    }
}
```

**启动时，Spring 容器会调用 `matches()` 方法，返回 `false` 就直接跳过这个 Bean 的定义，连类都不会加载。** 这就是 `@Conditional` 的核心：**容器启动时的一次性决策，决定了 Bean 的生死。**

---

## 设计哲学：为什么不是 if-else 或 @Profile？

### 1. 非侵入式扩展
Spring 的设计哲学是“约定优于配置”，`@Conditional` 让你用注解表达意图，而不需要修改业务代码。`@Profile` 是它的特例——本质上 `@Profile` 就是 `@Conditional(ProfileCondition.class)`，`ProfileCondition` 检查 `spring.profiles.active` 属性。但如果你需要检查类路径、自定义属性、甚至系统环境变量，`@Profile` 就无能为力了。

### 2. 声明式 vs 命令式
用 `if-else` 在 `@Configuration` 的构造方法或 `@PostConstruct` 里判断并抛异常，虽然也能阻止 Bean 创建，但这种方式是命令式的、隐式的，而且会污染业务逻辑。`@Conditional` 把条件判断提升到了容器解析 Bean 定义的阶段，与 Bean 的生命周期完全解耦。

### 3. 与自动配置无缝集成
Spring Boot 的自动配置（`@EnableAutoConfiguration`）本质上是一堆 `@Configuration` 类，它们通过 `spring.factories` 加载，然后各自用 `@ConditionalOnClass`、`@ConditionalOnProperty` 等注解控制是否生效。这样，第三方 starter 可以声明“我只有在类路径有 RedisTemplate 时才启用”，完全无需用户手动开关。

---

## 深入源码：Condition 接口与评估时机

`Condition` 接口只有一个方法：

```java
public interface Condition {
    boolean matches(ConditionContext context, AnnotatedTypeMetadata metadata);
}
```

- `ConditionContext`：提供访问 `BeanFactory`、`Environment`、`ResourceLoader`、`ClassLoader` 的能力。你可以用它检查某个类是否存在（`ClassUtils.isPresent()`）、读取属性、甚至获取注册的 Bean 定义。
- `AnnotatedTypeMetadata`：可以获取被标注元素（类或方法）上的注解信息，让你在条件中根据其他注解做决策。

**高级开发者容易误解的点**：

> 🔍 **精确说明**：条件评估是在容器启动的“配置类解析阶段”进行的，而不是在 Bean 实例化时。对于单例 Bean，条件只评估一次；对于 prototype Bean，虽然每次获取都会重新评估，但实际中很少对 prototype 使用条件，因为性能开销大且语义模糊。

**条件评估的顺序**：多个 `@Conditional` 注解是“与”关系，全部满足才注册。但不同 `Condition` 实现之间没有执行顺序保证，除非你通过 `@AutoConfigureAfter` / `@AutoConfigureBefore` 控制自动配置类的加载顺序。Spring 内部使用 `ConditionEvaluator` 来缓存评估结果（`Map<AnnotatedTypeMetadata, Boolean>`），避免重复调用。

**条件与 `@Configuration` 的关系**：`@Conditional` 可以放在 `@Configuration` 类上，此时整个配置类下的所有 `@Bean` 方法都受条件控制；也可以放在单个 `@Bean` 方法上，只控制该 Bean。Spring Boot 的自动配置类通常将 `@ConditionalOnClass` 放在类级别，因为如果缺少依赖，整个配置类都不应被解析。

---

## 前端类比：Vue 的条件渲染（但只到一半）

如果你熟悉 Vue 3，你可能会想：“这不就是 `v-if` 吗？” 确实，两者都在“运行时根据条件选择资源”——Vue 用 `v-if` 决定组件是否渲染，Spring 用 `@Conditional` 决定 Bean 是否注册。但这里有一个关键差异：

**Vue 的条件渲染是渲染时的 UI 切换，即使组件不渲染，它的代码模块仍然会被打包加载。而 Spring 的条件注册是容器启动时的 Bean 创建决策，如果条件不满足，连该 Bean 的类都不会被加载到 JVM 中。**

```vue
<script setup>
import { ref, computed } from 'vue'
import MemoryDbComponent from './MemoryDbComponent.vue'
import ProductionDbComponent from './ProductionDbComponent.vue'

const useMemoryDb = ref(import.meta.env.VITE_USE_MEMORY_DB === 'true')
const activeComponent = computed(() => useMemoryDb.value ? MemoryDbComponent : ProductionDbComponent)
</script>

<template>
  <component :is="activeComponent" />
</template>
```

这段代码和前面的 Java 示例在“根据环境变量选择实现”这个意图上一致，但 Vue 的 `import` 语句在构建时就已经将两个组件都打包进 bundle 了，而 Spring 的 `@Conditional` 在启动时如果条件失败，连 `memoryDataSource()` 方法所在的类都不会被加载（前提是条件标注在 `@Bean` 方法上，且该类不是由其他方式加载的）。

**类比止步点**：Vue 的条件渲染是为了运行时 UI 动态切换，Spring 的条件注册是为了启动时安全决策——避免因依赖缺失导致启动失败。如果你把两者等同，就会在调试启动错误时产生误解：Vue 的条件渲染不会阻止组件代码加载，而 Spring 的条件注册会完全跳过 Bean 的创建。

---

## Spring Boot 的预置条件注解家族

Spring Boot 在 `@Conditional` 基础上扩展了一组开箱即用的注解，它们本质上是 `@Conditional` + 预定义的 `Condition` 实现：

| 注解 | 作用 | 底层 Condition 类 |
|------|------|-------------------|
| `@ConditionalOnClass` | 类路径存在指定类 | `OnClassCondition` |
| `@ConditionalOnMissingClass` | 类路径不存在指定类 | `OnClassCondition` |
| `@ConditionalOnBean` | 容器中已存在指定 Bean | `OnBeanCondition` |
| `@ConditionalOnMissingBean` | 容器中不存在指定 Bean | `OnBeanCondition` |
| `@ConditionalOnProperty` | 指定属性存在且值匹配 | `OnPropertyCondition` |
| `@ConditionalOnResource` | 指定资源文件存在 | `OnResourceCondition` |
| `@ConditionalOnWebApplication` | 当前是 Web 应用 | `OnWebApplicationCondition` |
| `@ConditionalOnExpression` | SpEL 表达式为 true | `OnExpressionCondition` |

这些注解通常用在自动配置类上，通过 `spring.factories` 机制加载。例如 Spring Boot 的 `RedisAutoConfiguration`：

```java
@Configuration
@ConditionalOnClass(RedisTemplate.class) // 只有类路径有 RedisTemplate 才生效
public class RedisAutoConfiguration {
    @Bean
    @ConditionalOnMissingBean(name = "redisTemplate")
    public RedisTemplate<String, Object> redisTemplate(RedisConnectionFactory factory) {
        // ...
    }
}
```

**条件评估的时机**：这些 `Condition` 实现都实现了 `AutoConfigurationImportFilter` 接口，可以在自动配置类被真正解析之前就进行过滤，从而避免无意义的类加载。

---

## 设计权衡与决策指南

| 优势 | 代价 |
|------|------|
| 避免启动失败：依赖缺失时优雅跳过，而非抛出 ClassNotFoundException | 条件评估增加启动时间（通常可忽略，但复杂条件会累积） |
| 灵活按环境/配置开关：无需修改代码，仅改配置文件或类路径 | 条件逻辑可能分散，导致配置难以全局理解 |
| 与 Spring 生态无缝集成：支持自动配置、Profile、属性等 | 条件复杂时调试困难，需查看自动配置报告（`--debug`） |
| 声明式编程：用注解表达意图，比 if-else 更清晰 | 条件实现类需手动管理，可能产生大量小类 |

**何时该用**：
- 项目依赖可选库（如消息队列、缓存），需要根据运行时类路径自动启用/禁用配置。
- 多环境部署（开发/测试/生产）需要不同 Bean 实现，且环境差异不仅限于 profile 名。
- 希望让第三方库的自动配置按条件生效（如 Spring Boot 的 starter）。

**何时不该用**：
- 条件逻辑简单且固定（如仅根据一个 profile），用 `@Profile` 即可。
- 条件依赖运行时动态变化的数据（如用户请求），因为条件只在容器启动时评估一次。
- 条件实现过于复杂（如需要远程调用），会严重拖慢启动速度，此时应考虑懒加载或策略模式。

**与其他方案对比**：
- `@Profile`：是 `@Conditional` 的特例，只能基于 `spring.profiles.active`，无法检查类路径或自定义属性。
- `@Import` + `ImportSelector`：也可以实现条件导入，但更底层，需要手动实现 `ImportSelector` 接口，且无法享受 `@Conditional` 的声明式语法糖。
- Java 8 的 `Optional` + 运行时检查：需要手动在 `@PostConstruct` 中判断并抛出异常，不如 Spring 的条件机制优雅。

---

## 实践建议（高级开发者的工具箱）

1. **善用自动配置报告**：启动时加 `--debug` 参数，可以查看哪些自动配置类被匹配/不匹配及其原因，快速定位条件失效问题。
2. **组合条件时注意顺序**：多个 `@Conditional` 注解是“与”关系，但不同 `Condition` 实现之间没有依赖顺序。如果条件间有依赖（如先检查类路径再检查属性），应合并到一个 `Condition` 实现中。
3. **避免在条件中做重量级操作**：例如不要查询数据库或调用远程服务，因为条件评估在容器启动时执行，会阻塞启动过程。如果需要，考虑使用 `@Lazy` 或 `ApplicationRunner`。
4. **使用 IDE 的条件视图**：IntelliJ IDEA 的 Spring 插件可以显示 Bean 的条件依赖，帮助可视化配置。
5. **条件类应无状态且线程安全**：`Condition` 实现通常是单例，不要在 `matches` 方法中修改共享状态。
6. **与 `@AutoConfigureAfter`/`@AutoConfigureBefore` 配合**：在 Spring Boot 自动配置中，使用这些注解控制条件评估的顺序，确保依赖的自动配置先被评估。

---

**回到最初的问题：为什么 Spring 要用 `@Conditional` 而不是 if-else？** 因为 if-else 是命令式的、侵入式的、且与容器生命周期无关；而 `@Conditional` 是声明式的、非侵入式的、与 IoC 容器深度绑定的。它让“按条件注册”成为 Spring 框架的一等公民，使得自动配置、环境隔离、可选依赖等场景变得优雅且可扩展。**记住：容器启动时的一次性决策，决定了 Bean 的生死——这就是 Spring 条件注册的核心哲学。**

---

### 系列导航

**上一篇**：[Service层：为什么业务逻辑必须独立于HTTP协议](#)
**下一篇**：[Spring Boot Filter：为什么HTTP请求处理必须分层拦截](#)

> 这是「前端工程师系统学 Java」系列第18篇，系统解读 Java 设计哲学（面向前端工程师）。