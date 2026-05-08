<!-- 控制性问题：为什么你的第一个 Spring Boot 类加上了 @Service，别人还是用不了？ -->

```java
// 你在 com.company.order 包下新建了一个文件，写上：
@Service
class OrderService {  // 处理订单逻辑
    public void createOrder() { ... }
}
```

代码提交后，同事在 `controller` 包下想注入你的服务，结果编译直接炸了——"OrderService 不可见"。**这不是 Spring 的锅，是 Java 类定义有两条硬性规则你没有遵守。**

> **记忆锚点：Java 用「文件名 = 类名」和「public = 对外承诺」这两条物理规则，把模块边界刻进了文件系统，编译器替你兜底。**

---

## 规则一：public 类必须独占同名文件

Java 的编译期有一条死规矩：**如果一个类被 `public`（意为"谁都可以用"）修饰，那它所在的 `.java` 文件必须跟它同名，而且一个文件里最多只能有一个 `public` 类。**

这意味着你打开项目文件夹，看到的 `.java` 文件列表，就是这个包对外的公开类清单——不需要打开任何文件，就能知道 `OrderService.java` 里一定有一个 `public class OrderService`。

**为什么这么设计？** 一为编译器，二为人。编译器看到 `import com.company.order.OrderService;`，能直接去 `order/OrderService.java` 找，不用遍历所有文件；你的同事看到包名，也知道去哪找你的类。几十个人的项目，这条规则节省的时间是按小时计的。

---

## 规则二：不加 public，就是包内部的事

如果你写了一个类但不加 `public`：

```java
// 文件名：InternalHelper.java
package com.company.order;

class InternalHelper {  // 没有 public，叫"包私有"
    public void doInternalWork() { ... }
}
```

那它就是**包私有**（default-access）的——只有 `com.company.order` 包里的其他类能用它，别的包连 `import` 都写不出来。这在大型项目里是个极有用的设计工具：你可以把复杂的业务逻辑拆成多个内部辅助类，只把少数几个 `public` 类暴露出去作为"API 门面"。以后重构内部类时，只要 `public` 类的接口不变，外面完全不受影响。

---

## 前端开发者熟悉的类比（但要止步于此）

如果你写过 Vue 或 React，这个思路应该不陌生：

```typescript
// Vue 3 中的类比
export function useOrder() {     // 对外暴露，相当于 Java 的 public 类
    calculateTax(100)            // 调用同文件里的内部函数
}

function calculateTax(amount: number) {  // 没有 export，内部实现
    return amount * 0.1
}
```

Vue 里你故意不写 `export`，和 Java 里没加 `public` 的类一样，都是在说"这是内部实现，外部模块别直接用"。**区分公开接口和内部细节，这个思想是共通的。**

⚡ 但类比到此为止——**前端完全没有「文件名必须等于导出名」这样的物理约束。** Vue 中你把 `useOrder.ts` 改成 `orderHelpers.ts`，里面的 `export function useOrder` 照样能正常工作。但在 Java 里，你把 `OrderService.java` 改成 `Services.java`，编译器当场拒绝编译。这种把模块边界刻进文件系统的做法，是 Java 独有的工程选择。

---

## 两个新手一定会踩的坑

### 坑 1：重命名文件不同步改类名

```java
// 文件名：Services.java
@Service
public class OrderService { ... }  // 类名和文件名对不上
```

编译器直接报错：`OrderService is public, should be declared in OrderService.java`。程序根本启动不了，Spring 容器连扫描的机会都没有。

🔎 精确说明：IDE 的重构功能会自动同步文件名和类名，但手动重命名时必须两边一起改。

### 坑 2：忘了给跨包使用的类加 public

```java
// 文件名：OrderService.java（com.company.order 包下）
@Service
class OrderService { ... }  // 包私有
```

```java
// 文件名：OrderController.java（com.company.controller 包下）
import com.company.order.OrderService;  // 编译错误！
```

`controller` 包和 `order` 包是不同的包，包私有类对别包不可见。结果就是连 `import` 都写不出来，更别提 `@Autowired` 注入了。

**这两个坑的共同根源，都是违反了开头那条物理规则：「别人要用的类必须 public，public 类必须跟文件同名。」**

---

## 实战：一个可运行的完整示例

```java
// 文件：src/main/java/com/example/demo/service/OrderService.java
package com.example.demo.service;

import org.springframework.stereotype.Service;

@Service
public class OrderService {       // ✅ public 且文件名一致
    public void createOrder() {
        System.out.println("订单已创建");
    }
}

// 同一个包内部使用的辅助类，不需要 public
class InternalHelper {            // ✅ 包私有，避免外部误用
    public void validate() { ... }
}
```

```java
// 文件：src/main/java/com/example/demo/controller/OrderController.java
package com.example.demo.controller;

import com.example.demo.service.OrderService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderController {
    private final OrderService orderService;

    @Autowired
    public OrderController(OrderService orderService) {  // Spring 自动注入
        this.orderService = orderService;
    }

    @GetMapping("/order")
    public String newOrder() {
        orderService.createOrder();
        return "success";
    }
}
```

启动应用，访问 `/order`，控制台输出"订单已创建"。**物理规则遵守了，Spring 容器才能正确工作——这是地基。**

---

## 动手验证

最快的验证方式，是故意破坏规则看看效果：

1. 在 IDEA 里新建 `OrderService.java`，写好 `public class OrderService`，启动成功
2. 把文件名改成 `Services.java`，编译器立刻报错
3. 把上面的 `public` 删掉，另一个包下的 Controller 立刻挂掉
4. 恢复原样，再把 `InternalHelper` 改成 `public`，IDEA 会警告你应该把它移到一个同名文件

⚠️ 所有验证都在编译阶段就失败了，不需要等到运行期——这就是 Java 用"编译器强制规则"来保护团队协作的方式。

> **记忆锚点回扣：Java 的类定义不是随意写在某个文件里就行——文件名就是类名，public 就是对外承诺，编译器帮你检查这两条，从源头上杜绝了"找不到类"和"误用内部实现"的混乱。**