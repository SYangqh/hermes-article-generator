<!-- 控制性问题：如何用 `@RestControllerAdvice` 让 Spring Boot 项目的异常处理不再重复、混乱？ -->

你在 Spring Boot 项目里写 Controller 时，一定遇到过这个场景：每个方法里都塞着 try-catch，有的返回 `{code: 400, message: "xxx"}`，有的直接 return 字符串，有的把 HTTP 状态码设为 200 但 body 里塞错误信息。前端 Axios 拦截器一脸懵——到底该按状态码判断还是按 body 里的字段？**这不是代码质量问题，是缺少一个强制边界：让所有异常处理集中在同一处，输出统一格式。** `@RestControllerAdvice` 就是那个边界，编译器替你兜底。

---

### 你会在哪里遇到这个问题？

一个典型的 Spring Boot 项目，Controller 数量一多，异常处理就失控了。

```java
// 反例：每个 Controller 自己处理异常
@RestController
public class UserController {
    @PostMapping("/user")
    public ApiResponse createUser(@RequestBody @Valid UserDTO dto) {
        try {
            userService.create(dto);
            return ApiResponse.success(null);
        } catch (MethodArgumentNotValidException e) {
            return ApiResponse.error(400, "参数错误");
        } catch (BusinessException e) {
            return ApiResponse.error(e.getCode(), e.getMessage());
        } catch (Exception e) {
            return ApiResponse.error(500, "系统异常");
        }
    }
}
```

另一个 Controller 可能直接返回 `ResponseEntity`，第三个可能抛异常给容器返回默认错误页面。**后果：前端无法统一解析错误，后端每新增一个 Controller 都要重复写 try-catch，维护成本直线上升。**

---

### 这个机制解决了什么问题？

**统一异常处理与响应格式。** `@RestControllerAdvice` 将原本散落在各个 Controller 中的异常处理逻辑集中到一处，确保所有异常（包括 Spring 内部抛出的）都能被捕获，并转换为前后端约定好的统一 JSON 结构。同时让 HTTP 状态码回归语义：4xx 表示客户端错误，5xx 表示服务端错误，不再滥用 200 来承载业务错误。

> 🔍 **记忆锚点**：**一处定义，全局生效**。所有 Controller 自动享受同一个异常处理逻辑，不再各自为政。

---

### Java 为什么这样设计？

Spring 早期版本提供了 `@ExceptionHandler` 注解（用于在单个 Controller 内部定义异常处理方法），但依然无法避免重复——每个 Controller 都要写一遍类似的逻辑。更糟的是，如果一个异常没有被任何 `@ExceptionHandler` 捕获，它会直接传播到 Servlet 容器，返回默认错误页面或 500 响应，完全不可控。

Spring 的设计者借鉴了 **AOP（面向切面编程）** 的思想——将横切关注点（异常处理）从业务逻辑中剥离，通过一个全局切面来拦截异常。`@ControllerAdvice`（及其 REST 版本 `@RestControllerAdvice`）就是这个切面的载体。它本质上是一个特殊的 `@Component`（Spring 管理的 Bean），其中的 `@ExceptionHandler` 方法会被 Spring 注册为全局异常处理器，作用于所有 Controller。

这种设计解决了两个工程问题：
1. **代码复用**：异常处理逻辑只需写一次，所有 Controller 共享。
2. **响应一致性**：所有异常都经过同一个处理器，输出格式完全可控。

**这就引出一个问题**——Spring 如何找到正确的异常处理器？答案是**基于异常类型继承树匹配**。

---

### 核心代码示例

先定义统一响应体和自定义业务异常（继承 `RuntimeException`，方便全局捕获）：

```java
// 统一响应体
public class ApiResponse<T> {
    private int code;      // 业务状态码，0=成功，非0=错误
    private String message;
    private T data;
    // 构造方法、getter/setter 省略
    public static <T> ApiResponse<T> success(T data) {
        return new ApiResponse<>(0, "success", data);
    }
    public static <T> ApiResponse<T> error(int code, String message) {
        return new ApiResponse<>(code, message, null);
    }
}

// 自定义业务异常（继承 RuntimeException，非受检异常）
public class BusinessException extends RuntimeException {
    private int code;
    public BusinessException(int code, String message) {
        super(message);
        this.code = code;
    }
    public int getCode() { return code; }
}
```

再写全局异常处理器：

```java
@RestControllerAdvice
public class GlobalExceptionHandler {

    // 处理参数校验异常（Spring 自带的 MethodArgumentNotValidException）
    @ExceptionHandler(MethodArgumentNotValidException.class)
    @ResponseStatus(HttpStatus.BAD_REQUEST)  // 强制返回 400 状态码
    public ApiResponse<Void> handleValidation(MethodArgumentNotValidException ex) {
        String msg = ex.getBindingResult().getFieldErrors().stream()
                .map(e -> e.getField() + ": " + e.getDefaultMessage())
                .collect(Collectors.joining("; "));
        return ApiResponse.error(400, msg);
    }

    // 处理业务异常，使用 ResponseEntity 动态设置状态码
    @ExceptionHandler(BusinessException.class)
    public ResponseEntity<ApiResponse<Void>> handleBusiness(BusinessException ex) {
        ApiResponse<Void> body = ApiResponse.error(ex.getCode(), ex.getMessage());
        // 根据业务 code 决定 HTTP 状态码，这里统一用 400 演示
        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(body);
    }

    // 兜底：处理其他所有未预料到的异常
    @ExceptionHandler(Exception.class)
    public ApiResponse<Void> handleUnknown(Exception ex) {
        log.error("Unexpected error", ex);  // 务必记录日志
        return ApiResponse.error(500, "服务器内部错误");
    }
}
```

**代码意图说明**：
- `handleValidation` 使用 `@ResponseStatus` 注解（首次出现：`@ResponseStatus` 用于指定异常处理方法的默认 HTTP 状态码）固定返回 400，方法返回 `ApiResponse`，但 HTTP 状态码被强制设为 400。
- `handleBusiness` 使用 `ResponseEntity`（首次出现：`ResponseEntity` 是 Spring 提供的 HTTP 响应对象，可自定义状态码、头、body），动态设置状态码。注意：如果方法返回 `ResponseEntity`，`@ResponseStatus` 会被忽略。
- `handleUnknown` 兜底，返回 500，确保任何未捕获异常都不会泄漏给容器。

> 🔍 **精确说明**：匹配规则是基于异常类型继承树的。Spring 会找到能够处理该异常的最具体的 `@ExceptionHandler` 方法。例如，抛出 `BusinessException`（继承自 `RuntimeException`）时，优先匹配 `handleBusiness`，而不是 `handleUnknown(Exception.class)`。如果找不到精确匹配，则向上查找父类异常对应的处理器。

**记忆锚点回扣**：**一处定义，全局生效**——以上三个方法覆盖了所有 Controller 可能抛出的异常，无需在每个 Controller 里重复 try-catch。

**异常处理流程概览**：

```mermaid
graph TD
    A[Controller 抛出异常] --> B{全局异常处理器}
    B --> C[匹配具体异常类型]
    C --> D[执行对应@ExceptionHandler方法]
    D --> E[构造统一响应体]
    E --> F[返回给客户端]
```

---

### 设计权衡：@ResponseStatus vs ResponseEntity

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| 只使用 `@ResponseStatus` 注解 | 代码简洁，无需创建 `ResponseEntity` | 状态码固定，无法根据异常动态改变 | 异常类型与状态码一一对应（如 `MissingServletRequestParameterException` -> 400） |
| 只使用 `ResponseEntity` | 完全控制状态码、header、body | 代码稍多，每个方法都要构造 `ResponseEntity` | 需要动态设置状态码（如业务异常根据 code 决定是 400 还是 200） |
| 混合使用 | 灵活，可覆盖默认状态码 | 容易混淆优先级，需注意 `ResponseEntity` 会覆盖 `@ResponseStatus` | 大多数项目推荐：用 `@ResponseStatus` 设置默认状态码，在需要动态时改用 `ResponseEntity` |

**何时该用 / 何时不该用**：
- **必须用**：任何需要对外提供 REST API 的 Spring Boot 项目，都应该配置一个全局异常处理器。
- **不该用**：如果项目只是内部微服务之间调用，且错误处理已由框架层（如 Feign 的 fallback）处理，可以不使用。但即便如此，统一处理仍然有助于日志和监控。

**异常匹配优先级陷阱**：
如果你定义了两个方法分别处理 `RuntimeException` 和 `BusinessException`，Spring 会正确选择 `BusinessException` 的处理器。但如果 `BusinessException` 没有对应的处理器，则会匹配到 `RuntimeException` 的处理器（因为 `BusinessException` 继承自 `RuntimeException`）。**注意**：如果同一个异常类型有多个处理器（比如在两个不同的 `@ControllerAdvice` 中），Spring 会按照 `@Order` 或 `Ordered` 接口的顺序选择第一个匹配的，但通常不建议这样做，容易混乱。

> 🔍 **记忆锚点**：**一处定义，全局生效**——但要注意匹配优先级，避免意外兜底。

---

### 如果你熟悉前端：这有点像 Axios 拦截器 + Vue 全局错误处理器

前端也有类似的“集中处理异常”需求。在 Vue 3 或 React 项目中，你会在 Axios 响应拦截器里统一处理网络错误，在 Vue 的 `app.config.errorHandler` 里捕获组件渲染错误。**这和 `@RestControllerAdvice` 的工程动机完全一致**：将散落的异常处理收敛到一个中心点，确保错误响应格式一致（前端是 UI 提示，后端是 JSON 结构）。

```javascript
// Axios 响应拦截器（类比 @ExceptionHandler 处理特定异常类型）
service.interceptors.response.use(
  response => {
    const res = response.data
    if (res.code !== 0) { // 业务错误
      ElMessage.error(res.message)
      return Promise.reject(new Error(res.message))
    }
    return res
  },
  error => { // HTTP 状态码错误
    if (error.response.status === 401) { /* 跳转登录 */ }
    else { ElMessage.error('服务器错误') }
    return Promise.reject(error)
  }
)
```

**共同本质**：将原本散落在各处的异常处理逻辑收敛到一个中心点，降低维护成本。**但类比止步于此**——Vue 的全局错误处理器只捕获 UI 渲染错误（类似 Java 的 `Exception.class` 兜底），不处理网络请求错误；而 Java 的 `@RestControllerAdvice` 同时覆盖了这两者（通过 `@ExceptionHandler` 和 `ResponseEntity`）。如果你把前端全局错误处理器等同于 Java 的全局异常处理器，会误解其职责范围。

---

### 实践建议

1. **定义统一响应类**：使用泛型 `ApiResponse<T>`，包含 `code`、`message`、`data`，并提供静态工厂方法（如 `success(data)`、`error(code, message)`）。
2. **合理使用 `@ResponseStatus` 和 `ResponseEntity`**：对于固定映射的异常（如 `MissingServletRequestParameterException` -> 400），用 `@ResponseStatus` 更简洁；对于需要动态 code 的业务异常，用 `ResponseEntity`。
3. **不要吞异常**：在全局处理器中，务必记录日志（`log.error`），尤其是兜底的 `Exception` 处理器，否则问题难以排查。
4. **避免在全局处理器中处理所有异常**：有些异常需要特殊处理（如 `AccessDeniedException` 应返回 403），建议单独定义方法，而不是全部交给 `Exception.class` 兜底。
5. **利用 `@Order` 控制多个 Advice 的执行顺序**：如果项目中有多个 `@ControllerAdvice`，可以通过 `@Order(1)` 等指定优先级，数字越小优先级越高。通常只用一个全局 Advice 即可。
6. **编码规范**：项目中所有自定义异常都应继承 `RuntimeException`（非受检异常），避免在 Controller 方法签名中声明 throws。这样可以让全局处理器捕获，而不是强迫调用方处理。

最后回到最初的反例——如果用 `@RestControllerAdvice`，Controller 可以干净地只写业务逻辑：

```java
@RestController
public class UserController {
    @PostMapping("/user")
    public ApiResponse<Void> createUser(@RequestBody @Valid UserDTO dto) {
        userService.create(dto);
        return ApiResponse.success(null);  // 异常全部交给全局处理器
    }
}
```

**记忆锚点最终回扣**：**一处定义，全局生效**。从此，你的 Spring Boot 项目异常处理不再重复、混乱。

---

### 系列导航

**上一篇**：[Filter：为什么横切关注点必须在DispatcherServlet之前拦截](#)
**下一篇**：[RestTemplate：为什么HTTP客户端必须由Spring统一封装](#)

> 这是「前端工程师系统学 Java」系列第13篇，系统解读 Java 设计哲学（面向前端工程师）。