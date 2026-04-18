**知识点名称**  
ForkJoinPool 的工作窃取（Work-Stealing）调度器设计：以 `ForkJoinTask` 状态机与双端队列（Deque）协同实现无锁任务分发的分布式负载均衡机制  

**设计核心**  
ForkJoinPool 的本质不是“并行线程池”，而是一个**面向递归分解型计算的、基于局部性感知的分布式调度器**。其核心设计思想包含三层权衡：  
1. **空间局部性优先于线程绑定**：放弃传统线程池的“任务→线程”静态映射，改用“线程→双端队列”绑定 + 跨队列窃取，使子任务天然倾向在生成它的线程本地执行（减少缓存失效）；  
2. **无锁调度的确定性代价**：通过 `@Contended` 隔离 `WorkQueue` 的 `top/bottom` 指针，配合 `Unsafe.compareAndSet` 实现单生产者/多消费者（SPMC）语义，牺牲部分内存占用换取无锁吞吐，但强制要求 `top` 仅由 owner 线程修改，`bottom` 由 owner 写、stealer 读——此约束直接定义了状态机跃迁边界；  
3. **递归分解的终止条件内化**：`ForkJoinTask` 不是被动执行单元，而是主动参与调度决策的状态机——`doExec()` 返回 `true` 仅当任务已执行完毕或被取消，否则必须 `fork()` 或 `join()`，将控制流交还调度器；该协议使 ForkJoinPool 能在不依赖外部中断机制下，实现 O(1) 时间复杂度的“任务完成传播”。  

**Java 原理 + 代码**  

```java
// 核心原理：WorkQueue 的无锁双端队列设计（精简自 JDK 17 ForkJoinPool.java）
// 注意：真实实现使用 @Contended 分隔字段，此处用注释标明内存布局意图
static final class WorkQueue {
    // @Contended("q") —— 强制 top/bottom 位于独立缓存行，避免伪共享
    volatile int top;        // owner 线程独占写（push/pop 时递减），stealer 可读（只用于窃取判断）
    int base;                // 仅 owner 修改，记录队列起始位置（实际未直接使用，由 bottom 间接体现）
    int capacity;            // 队列容量（2 的幂次）
    
    // 底层数组采用懒初始化，避免无谓内存分配
    ForkJoinTask<?>[] array; // volatile 数组引用，确保数组内容可见性
    
    // owner 线程 push 任务（LIFO，保证局部性）
    final void push(ForkJoinTask<?> task) {
        ForkJoinTask<?>[] a; int s, m;
        if ((a = array) != null && (m = a.length - 1) >= 0) {
            // bottom 是当前可写位置（从 0 开始递增），top 是可读栈顶（从 0 开始递减）
            // 注意：此处 s = ++bottom，但 bottom 字段本身是非 volatile 的！
            // 真实实现中 bottom 是普通 int，靠 Unsafe.storeFence() + volatile array 保证发布顺序
            int s = (int)(--top & m); // 使用负数索引实现栈顶向下增长（优化 CPU 预取）
            U.putObject(a, ((long)s << ASHIFT) + ABASE, task); // UNSAFE 直接写入
            // 关键屏障：确保 task 写入对其他线程可见（volatile array + storeFence）
            U.storeFence();
        }
    }

    // owner 线程 pop 任务（LIFO，高局部性）
    final ForkJoinTask<?> pop() {
        ForkJoinTask<?>[] a; ForkJoinTask<?> t; int m;
        if ((a = array) != null && (m = a.length - 1) >= 0) {
            int s = top;
            if (s != base) { // 非空队列
                long j = ((long)(s - 1) & m) << ASHIFT; // 计算栈顶前一个位置
                t = (ForkJoinTask<?>)U.getObject(a, j);
                if (t == null) return null; // 竞态：被窃取者抢先取走
                if (U.compareAndSetObject(a, j, t, null)) {
                    top = s - 1; // 仅 owner 修改 top，无竞争
                    return t;
                }
            }
        }
        return null;
    }

    // stealer 线程窃取任务（FIFO，跨线程负载均衡）
    final ForkJoinTask<?> poll() {
        ForkJoinTask<?>[] a; ForkJoinTask<?> t; int b, m;
        if ((a = array) != null && (m = a.length - 1) >= 0) {
            b = base; // stealer 读 base（非 volatile，但由 owner 在 push 后更新）
            if (b - top > 0) { // 队列至少有 2 个任务（防止 owner 正在 pop 中断）
                long j = ((long)b & m) << ASHIFT; // FIFO：从 base 位置取
                t = (ForkJoinTask<?>)U.getObjectVolatile(a, j);
                if (t != null && base == b && 
                    U.compareAndSetObject(a, j, t, null)) {
                    // 关键：原子更新 base，且需验证 base 未被 owner 修改（owner 可能已 push 新任务）
                    if (U.compareAndSetInt(this, BASE, b, b + 1))
                        return t;
                }
            }
        }
        return null;
    }
}

// ForkJoinTask 状态机协议：exec() 的返回值直接驱动调度器状态跃迁
abstract static class ForkJoinTask<V> implements Future<V>, Serializable {
    // 状态字段：volatile 保证跨线程可见，但状态跃迁必须符合严格协议
    volatile int status; // 0: NEW, -1: NORMAL, -2: CANCELLED, -3: EXCEPTIONAL

    // 调度器核心契约：doExec() 必须返回 true 当且仅当任务已彻底完成（包括异常处理）
    // false 表示任务尚未执行，或已 fork/join，需调度器重新入队或阻塞等待
    final boolean doExec() {
        int s; boolean completed;
        if ((s = status) < 0) // 已完成或取消，直接返回
            completed = (s == NORMAL);
        else {
            try {
                // 子类实现：若为 compute() 方法，此处执行实际逻辑
                // 若内部调用 fork()，则任务被压入当前线程队列，doExec 返回 false
                // 若内部调用 join()，则触发阻塞等待，但 join() 内部会先尝试窃取其他任务
                completed = exec(); 
            } catch (Throwable rex) {
                setException(rex); // 设置状态为 EXCEPTIONAL，并唤醒等待者
                completed = false;
            }
        }
        // 状态跃迁：仅当任务真正完成时，才设置为 NORMAL
        if (completed && U.compareAndSetInt(this, STATUS, s, NORMAL))
            return true;
        return false;
    }

    // 关键：exec() 是抽象钩子，但必须遵守「不阻塞、不等待」原则
    // 所有阻塞行为（如 join）必须由 ForkJoinPool 内部统一处理，确保调度器始终可控
    protected abstract boolean exec();
}
```

**关键原理解析**  
- `WorkQueue.top` 与 `base` 的分离设计，使 owner 线程的 `push/pop`（LIFO）与 stealer 的 `poll`（FIFO）形成天然隔离：LIFO 保障递归子任务局部性，FIFO 保障窃取者获得最老任务（降低饥饿概率）；  
- `poll()` 中双重 `compareAndSet`（先数组元素，再 `base` 字段）是唯一允许 stealer 修改 `base` 的路径，且必须验证 `base` 未变，这杜绝了 owner 在 `push` 过程中 `base` 被并发修改的风险；  
- `ForkJoinTask.status` 的状态机设计将任务生命周期完全交由调度器管理：`doExec()` 返回 `false` 时，任务必然已被 `fork()` 入队或进入 `join()` 阻塞，调度器据此决定是继续执行下一个任务，还是切换到窃取模式——此协议使 ForkJoinPool 在无任何锁和条件变量的情况下，实现 O(1) 平均任务分发延迟。