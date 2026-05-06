**Spring Validation 请求校验流程**

```mermaid
graph TD
    A[客户端HTTP请求] --> B[Controller方法]
    B --> C{@Valid/@Validated触发}
    C -->|校验通过| D[执行业务逻辑]
    C -->|校验失败| E[抛出MethodArgumentNotValidException]
    E --> F[@RestControllerAdvice处理]
    F --> G[返回标准化错误响应]
```

<!-- 控制性问题：为什么 Spring 要用声明式注解来做校验，而不是手动 if-else？ -->

```java
// 手动校验的噩梦
public ResponseEntity<?> createUser(@RequestBody UserDTO user) {
    if (user.getName() == null || user.getName().length() < 2 || user.getName().length() > 50) {
        return ResponseEntity.badRequest().body("用户名长度必须在2-50之间");
    }
    if (user.getEmail() == null || !user.getEmail().matches("^[A-Za-z0-9+_.-]+@[A-Za-z0-9.-]+$")) {
        return ResponseEntity.badRequest().body("邮箱格式错误");
    }
    // 每个接口重复类似代码，改规则时到处改，漏改一个就出 bug
    // ...
}
```

**核心论点：Spring Validation 通过声明式注解将校验逻辑从业务代码中彻底剥离，让框架自动触发校验并统一处理错误——你只需要在 DTO 字段上贴注解，编译器替你兜底。**

这就像你告诉 Spring：“用户名不能为空，长度 2-50，邮箱要合法”，然后 Spring 在请求到达 Controller 前自动帮你检查，不通过就直接返回标准化错误。你写业务逻辑时，拿到的数据**已经被保证是合法的**。

---

## 一、为什么需要这种“魔法”？

做 Spring Boot REST API 时，几乎每个接收请求体的接口都需要校验输入。如果每个方法都写 `if (user.getName() == null)` 这样的代码，你会面临三个问题：

1. **重复**：相同的校验逻辑在多个 Controller 方法中反复出现。
2. **不一致**：同一个字段的校验规则可能在不同接口中略有差异，导致前端困惑。
3. **难维护**：当规则变化时（比如用户名长度从 50 改为 100），你需要搜索所有引用了该字段的地方逐个修改，很容易遗漏。

> 🔍 **精确说明**：手动校验的另一个隐患是**异常处理不统一**——有的接口返回 400，有的返回 422，错误消息格式也不一样，前端对接时苦不堪言。

**记忆锚点：声明式注解的核心价值——用“贴标签”代替“写 if”，把校验的“什么规则”和“何时执行”彻底分离。**

---

## 二、Java 是怎么做到的？

### 2.1 三层架构

Spring Validation 基于 JSR 380（Bean Validation 2.0）规范，由 Hibernate Validator 作为默认实现。它分三层工作：

- **约束定义**：在 Java Bean 字段上使用 `@NotNull`、`@Size`、`@Pattern` 等注解声明规则。
- **触发校验**：通过 `@Valid` 或 `@Validated` 注解在 Controller 方法参数上启用，Spring MVC 自动调用 Validator。
- **异常处理**：校验失败抛出 `MethodArgumentNotValidException`，配合 `@RestControllerAdvice` 统一返回错误。

### 2.2 最容易被忽略的细节

**嵌套对象不会自动递归校验**。如果 DTO 里包含另一个对象字段，必须显式加 `@Valid`：

```java
public class UserDTO {
    @NotNull
    @Size(min = 2, max = 50)
    private String name;

    @Valid  // 没有这个，AddressDTO 里的 @NotNull 不会生效
    @NotNull
    private AddressDTO address;
}
```

**分组校验**：同一个 DTO 在不同场景（创建 vs 更新）下需要不同的约束集合。通过定义接口分组，并在 `@Validated` 中指定：

```java
public interface CreateGroup {}
public interface UpdateGroup {}

public class UserDTO {
    @NotNull(groups = CreateGroup.class)  // 只在创建时校验非空
    @Size(min = 2, max = 50, groups = CreateGroup.class)
    private String name;

    @Email(groups = CreateGroup.class)
    private String email;

    @NotNull(groups = UpdateGroup.class)  // 只在更新时校验非空
    private Long id;
}
```

Controller 中指定分组：

```java
@PostMapping
public ResponseEntity<?> create(@Validated(CreateGroup.class) @RequestBody UserDTO dto) { ... }

@PutMapping("/{id}")
public ResponseEntity<?> update(@Validated(UpdateGroup.class) @RequestBody UserDTO dto) { ... }
```

> 🔍 **精确说明**：`@Valid` 是 JSR 标准，只能触发校验；`@Validated` 是 Spring 扩展，支持分组，且可以放在类级别开启方法参数校验（如在 Service 层使用）。

### 2.3 自定义约束

当内置注解不够用时，你可以自定义。比如“密码必须包含数字和字母”：

```java
@Target({FIELD})
@Retention(RUNTIME)
@Constraint(validatedBy = PasswordComplexityValidator.class)
public @interface PasswordComplexity {
    String message() default "密码必须包含数字和字母";
    Class<?>[] groups() default {};
    Class<? extends Payload>[] payload() default {};
}

public class PasswordComplexityValidator implements ConstraintValidator<PasswordComplexity, String> {
    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        if (value == null) return true; // 非空由 @NotNull 处理
        return value.matches("^(?=.*[a-zA-Z])(?=.*\\d).+$");
    }
}
```

**记忆锚点：自定义约束把复杂的校验逻辑封装成注解，业务层永远只关心“贴标签”，不关心“怎么验”。**

### 2.4 统一异常处理

```java
@RestControllerAdvice
public class GlobalExceptionHandler {
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Map<String, Object>> handleValidation(MethodArgumentNotValidException ex) {
        Map<String, Object> errors = new HashMap<>();
        ex.getBindingResult().getFieldErrors().forEach(fe -> 
            errors.put(fe.getField(), fe.getDefaultMessage()));
        return ResponseEntity.badRequest().body(errors);
    }
}
```

这样前端收到的是 `{ "name": "长度必须在2-50之间", "email": "不是合法的邮箱地址" }` 这样的结构化错误，直接绑定到表单字段。

---

## 三、前端也有类似的“声明式校验”

如果你熟悉 Vue 或 React，你会发现 Spring Validation 的设计思想在前端完全镜像——**都是把校验规则从事件处理中分离，让框架自动触发并统一反馈**。

### Vue 3 + VeeValidate

```vue
<template>
  <Form @submit="handleSubmit" :validation-schema="schema">
    <Field name="name" rules="required|min:2|max:50" />
    <ErrorMessage name="name" />

    <Field name="email" type="email" rules="required|email" />
    <ErrorMessage name="email" />

    <Field name="password" rules="required|passwordComplexity" />
    <ErrorMessage name="password" />

    <button type="submit">提交</button>
  </Form>
</template>

<script setup>
import { Form, Field, ErrorMessage, defineRule } from 'vee-validate';

defineRule('passwordComplexity', (value) => {
  if (!value) return true;
  return /^(?=.*[a-zA-Z])(?=.*\d).+$/.test(value) || '密码必须包含数字和字母';
});

const schema = {
  name: 'required|min:2|max:50',
  email: 'required|email',
  password: 'required|passwordComplexity',
};

function handleSubmit(values) {
  console.log('提交数据', values);
}
</script>
```

- `rules` 字符串 = Java 的注解列表
- `defineRule` 自定义规则 = Java 自定义 `@PasswordComplexity` + Validator
- `Form` 组件 = Spring 的 `@Validated` 触发点

### React + React Hook Form + Zod

```tsx
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';

const userSchema = z.object({
  name: z.string().min(2).max(50),
  email: z.string().email(),
  password: z.string().regex(/^(?=.*[a-zA-Z])(?=.*\d).+$/, '密码必须包含数字和字母'),
});

type UserFormData = z.infer<typeof userSchema>;

function UserForm() {
  const { register, handleSubmit, formState: { errors } } = useForm<UserFormData>({
    resolver: zodResolver(userSchema),
  });

  const onSubmit = (data: UserFormData) => console.log('提交数据', data);

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <input {...register('name')} />
      {errors.name && <span>{errors.name.message}</span>}
      {/* ... */}
      <button type="submit">提交</button>
    </form>
  );
}
```

- `z.object({...})` = DTO 字段注解集合
- `z.string().min(2).max(50)` = `@NotNull` + `@Size(min=2, max=50)`
- `zodResolver` = 将 schema 注入校验引擎，类似 Spring 的 Validator

**共同本质**：都是**声明式校验框架**——开发者只声明“什么规则”，框架决定“何时校验”并收集错误。区别在于 Java 基于反射 + AOP，前端基于响应式系统或表单状态管理。

> ⚠️ **注意**：前端校验是增强用户体验的，**不能替代服务端校验**。用户可以直接绕过浏览器发送请求，服务端校验才是安全底线。

---

## 四、设计权衡与决策指南

| 方面 | Spring Validation | 前端方案 |
|------|------------------|----------|
| 声明方式 | 注解 | 规则字符串 / Zod schema |
| 触发时机 | 请求到达 Controller | 提交 / 失焦 / 实时 |
| 嵌套校验 | 需显式 `@Valid` | 自动递归 |
| 分组校验 | 通过接口分组 | 不同 schema / 条件规则 |
| 自定义规则 | 注解 + Validator | 自定义函数 / Zod refine |
| 性能 | 反射 + AOP（可忽略） | 运行时校验（通常无感知） |
| 错误处理 | 统一异常处理 | 统一 error 对象 + 模板 |

**什么时候该用声明式校验？**
- 任何需要约束输入数据的 REST API、表单提交、DTO 转换。
- 校验规则可能被多个接口共享，或需要分组。
- 团队希望统一错误响应格式。

**什么时候不该用？**
- 一次性简单校验（如一个接口只检查非空），直接写 `if` 更清晰。
- 校验依赖外部服务（如查数据库判断唯一性），应在 Service 层进行业务校验。
- 极端性能敏感场景（高频短连接），反射开销可能成为瓶颈（极少见）。

**记忆锚点：声明式校验的代价是引入框架抽象，但换来了“改一处、处处生效”的可维护性。对于中等以上复杂度的项目，这笔交易绝对划算。**

---

## 五、实践建议

1. **IDE 配置**：IntelliJ 自带 Hibernate Validator 插件，编写 DTO 时实时提示约束缺失。
2. **静态检查**：使用 Checkstyle 或 PMD 强制要求每个 DTO 字段至少有一个校验注解（特殊情况除外）。
3. **编码规范**：
   - 分组接口命名清晰：`CreateGroup`、`UpdateGroup`，避免 `Group1`。
   - 自定义约束的 `message` 使用国际化键（如 `{user.password.complexity}`），而不是硬编码。
   - 统一异常处理中除了 `field: error`，建议补充 `code` 和 `path`，便于前端定位。
4. **测试**：用 `MockMvc` 测试校验失败场景，确保自定义约束和分组逻辑正确。

---

**回到开头的问题：为什么 Spring 要用声明式注解来做校验？**  
因为手动 `if-else` 让校验散落在各处，难以复用、难以维护、难以统一。声明式注解把“什么规则”和“何时校验”彻底分离，你只需要在数据模型上贴标签，剩下的交给框架。**这是工程上对“重复”和“不一致”的最优雅反击。**

---

### 系列导航

**上一篇**：[WebMvcConfigurer：为什么MVC行为必须可编程式定制](#)  
**下一篇**：[Service层：为什么业务逻辑必须独立于HTTP协议](#)

> 这是「前端工程师系统学 Java」系列第 16 篇，系统解读 Java 设计哲学（面向前端工程师）。