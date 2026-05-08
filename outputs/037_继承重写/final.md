<!-- 控制性问题：为什么 Java 需要一个叫 @Override 的注解，它到底管什么用？ -->

```java
class VipOrderProcessor extends OrderProcessor {
    // 本意是重写父类的 process(Order) 方法
    public void process(String orderId) { ... }
}
```

这段代码编译通过，但运行时你写的逻辑永远不会被执行——订单处理静默走了父类空实现。

**核心论点：`@Override` 是一种“强制边界”——让编译器替你检查“你到底是不是在重写”。不是你说了算，是编译器说了算。**

---

## 没有 @Override 时，坑长什么样

假设你和同事协作一个订单系统。同事维护基础框架，定义了 `OrderProcessor` 父类（父类：被继承的那个类），提供了一个 `process(Order order)` 方法，默认只打日志：

```java
class OrderProcessor {
    public void process(Order order) {
        System.out.println("父类默认处理：" + order.getId());
    }
}
```

你负责 VIP 订单模块，写了一个子类（子类：用 `extends` 继承父类的那个类）去重写这个方法，加上发积分逻辑。但你手滑把参数类型写成了 `String`：

```java
class VipOrderProcessor extends OrderProcessor {
    // 没有 @Override —— 编译器不知道你想干嘛
    public void process(String orderId) {
        System.out.println("VIP 处理：" + orderId);
    }
}
```

调用时：

```java
Order order = new Order("ORDER-001");
OrderProcessor proc = new VipOrderProcessor();
proc.process(order);  // 输出：父类默认处理：ORDER-001
```

你写的 `process(String)` 根本没被调。这就是**静默失效**：编译不报错，运行时偷偷走错逻辑。

---

## @Override 到底干了什么

`@Override` 是一个 Java 注解（注解：给代码加标记，告诉编译器或工具“这段代码有特殊意图”）。它只有一行，却解决了一个工程核心问题：

> 🔍 **精确说明**：`@Override` 被加在一个方法上时，编译器会去父类找是否有方法签名完全一致的方法。找不到？直接编译报错。它不改变代码运行行为，纯粹是编译期守卫。

把刚才的错误写法加上 `@Override`：

```java
class VipOrderProcessor extends OrderProcessor {
    @Override  // 告诉编译器：我意图重写父类方法，请检查
    public void process(String orderId) {  // ❌ 编译报错！
        System.out.println("VIP 处理：" + orderId);
    }
}
```

编译器直接报：“方法不会覆盖或实现超类型的方法”。你不用等到上线、不用写单测、不用跑一遍才知道有问题——**按保存的那一刻就知道了**。

**这就是 @Override 的价值：把“靠肉眼对齐方法签名”升级为“编译器强制执行”。**

---

## 如果你熟悉 TypeScript，这和你用过的一个东西一模一样

TypeScript 从 4.3 开始引入了 `override` 关键字，设计动机与 Java 的 `@Override` 完全一致。

React 类组件场景：

```tsx
class BasePage extends React.Component<{ title: string }> {
    componentDidMount() {
        console.log('BasePage mounted');
    }
}

class HomePage extends BasePage {
    override componentDidMount() {  // 声明：我要重写父类方法
        super.componentDidMount();
        document.title = 'Home';
    }
}
```

一旦 `BasePage` 把 `componentDidMount` 改名成 `onMount`，所有子类的 `override` 那行直接标红，和 Java 的 `@Override` 效果等价。如果你是用 TypeScript 做项目的，可以把 `@Override` 理解成 `override` 的 Java 写法——**同一个设计选择，同一种编译期契约**。

> Vue 的组合式 API 没有类继承结构，不涉及这个场景。但如果项目中用到 TypeScript 类组件（比如 `vue-facing-decorator`），override 的用法和 React 一样。

---

## 重写的硬规则——编译器在查什么

`@Override` 检查的不是“看着像”，而是四条硬标准，必须全部满足才算重写：

| 检查项 | 规则 | 常见翻车 |
|--------|------|----------|
| 方法签名 | 方法名、参数个数、类型、顺序必须**完全一致** | `int` 写成 `Integer`——算不同 |
| 返回值 | 必须兼容（子类可返回父类返回值的子类型） | 父类返回 `Number`，子类返回 `String`——不兼容 |
| 访问权限 | 不能比父类更严 | 父类 `public`，子类写成 `protected`——报错 |
| 异常 | 抛出的受检异常不能更宽 | 父类抛 `IOException`，子类抛 `Exception`——报错 |

**最容易踩的一个坑**：参数类型 `int`（基本类型）和 `Integer`（包装类）不是同一个类型。父类写 `process(int id)`，子类写 `process(Integer id)`，不加 `@Override` 就是两个不同方法，一调就调错。

```java
class Parent {
    public void doSomething(int num) { }
}

class Child extends Parent {
    @Override
    public void doSomething(Integer num) { }  // ❌ 编译报错，签名不匹配
}
```

---

## 什么时候加，什么时候不加

**加 `@Override` 的场景：**
- 重写父类的普通方法
- 实现接口里定义的方法（同样建议加，防止未来接口方法被删了子类无感知）
- 重写父类的抽象方法

**不加的场景：**
- 你写的是全新的方法，和父类没任何关系
- 静态方法——静态方法属于类本身，不存在“多态重写”这回事，加上会直接编译报错

多态（多态：同一个方法调用，根据对象的真实类型执行不同子类的实现）只对实例方法生效。`@Override` 就是保护这种多态关系不会被意外切断的机制。

---

## 记忆力锚点

> **`@Override` = 把“我觉得我重写了”变成“编译器确认我重写了”。**

写代码时，你只需要做一件事：**只要本意是重写，就加上 `@Override`**。这是 Java 社区的编码铁律，IntelliJ IDEA 可以直接设置成“缺少 `@Override` 时标黄警告”，从工具层面堵住忘加的可能。

下次看到子类方法上没这个注解，脑子里就该拉响一个警报：这个方法是重写、重载、还是忘加了？