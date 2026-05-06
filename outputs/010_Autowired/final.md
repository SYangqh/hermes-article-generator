<!-- 控制性问题：为什么 Spring 项目里推荐用 @Autowired 而不是 new？ -->

```java
// 反例：硬编码依赖
public class UserService {
    private UserRepository userRepo = new UserRepository(); // 直接 new
    public void register(String name) {
        userRepo.save(name);
    }
}
```
这段代码能跑，但一旦你想把 `UserRepository` 从 MySQL 换成 Redis，或者想在单元测试里用 Mock 替代真实数据库——你必须打开 `UserService` 改源码，甚至可能牵连多个类。**这就是硬编码耦合的代价。**

**`@Autowired` 的核心理念很简单：你只需要声明“我需要一个 UserRepository”，Spring 会自动把对应的对象“注入”进来，你不用自己 `new`。** 这是 Spring 框架中最常用的依赖注入（Dependency Injection，简称 DI）机制，它帮你省掉了手动创建依赖对象的麻烦，同时让代码变得更灵活、更容易测试。

---

### 一句话理解 @Autowired

`@Autowired` 是一个注解（annotation，可以理解为给代码贴的“标签”），你把它写在字段或构造方法上，告诉 Spring：“嘿，这里需要某个类的实例，你帮我准备好，然后塞进来。” 而 Spring 在启动时会扫描所有被 `@Component`、`@Service`、`@Repository` 等注解标记的类（这些类被称为 Bean，即 Spring 容器管理的小组件），创建它们的实例，然后根据 `@Autowired` 的指示，把匹配的 Bean 自动赋值给对应的字段或构造参数。

**记忆锚点：@Autowired 强制解耦——你只管说“我要什么”，Spring 负责“给你什么”。**

---

### 从代码感受一下

最简单的用法是**字段注入**，直接在字段上加 `@Autowired`：

```java
@Component // 标记为 Spring 管理的 Bean
public class UserService {
    @Autowired
    private UserRepository userRepo; // Spring 自动注入

    public void register(String name) {
        userRepo.save(name);
    }
}
```

但 Spring 官方更推荐**构造器注入**，把 `@Autowired` 写在构造方法上：

```java
@Component
public class UserService {
    private final UserRepository userRepo; // final 表示不可变

    @Autowired
    public UserService(UserRepository userRepo) {
        this.userRepo = userRepo; // Spring 自动传入参数
    }

    public void register(String name) {
        userRepo.save(name);
    }
}
```

两种方式都能实现自动注入，但构造器注入有两大好处：
1. **依赖是 final 的**，一旦创建就不能改，避免运行时被意外替换。
2. **单元测试更方便**：你可以直接 `new UserService(mockRepo)` 传入 Mock 对象，不需要依赖 Spring 容器。

> 🔍 **精确说明**：字段注入通过 Java 反射机制绕过 `private` 限制赋值，而构造器注入是正常的对象创建流程。反射有一定性能开销（但只在启动时），且字段注入的依赖无法设为 `final`，测试时需要额外用 Spring 测试框架或反射工具。

---

### 前端开发者怎么看 @Autowired？

如果你写过 Vue 3，一定用过 `provide` / `inject`。在祖先组件里 `provide` 一个服务，后代组件用 `inject` 自动获取——这和 `@Autowired` 的“声明式获取依赖”非常像。

```vue
<!-- 祖先组件提供依赖 -->
<script setup>
import { provide } from 'vue'
import { createUserService } from './services/userService'
provide('userService', createUserService())
</script>

<!-- 后代组件自动注入 -->
<script setup>
import { inject } from 'vue'
const userService = inject('userService') // 类似 @Autowired
</script>
```

共同点：**你不需要手动创建或传递依赖，框架自动帮你找到并注入**，你只管用。

**但类比止步于此**。Vue 的 `provide/inject` 依赖组件树层级，必须显式 `provide` 才能 `inject`；而 Spring 的 `@Autowired` 依赖全局 IoC 容器（Inversion of Control 容器，即 Spring 管理所有 Bean 的“仓库”），框架启动时自动扫描并注入，不需要你手动 `provide`。另外，当同一个接口有多个实现时（比如 `UserRepository` 有 `MySQLUserRepo` 和 `RedisUserRepo`），Spring 可以用 `@Qualifier` 按名称指定注入哪个，而 Vue 的 `inject` 只能按字符串 key 匹配，无法自动按类型选择——这点 Spring 更强大。

---

### 新手最容易踩的坑

**忘记在类上加 `@Component`（或 `@Service`、`@Repository`）**。比如：

```java
public class UserService {
    @Autowired
    private UserRepository userRepo; // 这里会注入失败，因为 UserService 本身不是 Bean
}
```

`@Autowired` 只能注入 Spring 容器中已有的 Bean。如果 `UserService` 自己都没有被 Spring 管理，那么 `@Autowired` 根本不会生效，`userRepo` 会一直是 `null`，运行时抛出空指针异常（NullPointerException）。**记住：被注入的类和注入的目标类都必须是 Spring 管理的 Bean。**

---

### 日常 Spring Boot 项目中的典型场景

一个用户注册的后端服务，通常有三层：
- `Controller`（控制器，处理 HTTP 请求）
- `Service`（业务逻辑层）
- `Repository`（数据访问层）

每一层都通过 `@Autowired` 注入下一层的依赖，形成清晰的依赖链。当你需要更换数据库实现时，只需新增一个 `Repository` 实现类，并调整 `@Qualifier` 或 `@Primary`，业务代码完全不用改。单元测试时，直接 `new Service(mockRepo)` 就能测试业务逻辑，无需启动整个 Spring 容器。

**Spring 三层架构的依赖注入关系**

```mermaid
graph LR
    C[Controller] -->|@Autowired| S[Service]
    S -->|@Autowired| R[Repository]
```

**回到核心论点**：`@Autowired` 帮你省掉了 `new`，让代码从“硬编码依赖”变成“声明式依赖”。它强制解耦，让 Spring 替你兜底——这就是为什么 Spring 项目里推荐用它而不是手动 `new` 的原因。

---

### 系列导航

**上一篇**：[RestController：为什么REST API必须与控制器职责强绑定](#)
**下一篇**：[ConfigurationProperties：为什么配置必须类型安全且可验证](#)

> 这是「前端工程师系统学 Java」系列第 10 篇，系统解读 Java 设计哲学（面向前端工程师）。