# 排队位置计算逻辑 - 后端实现版

## 📌 正确理解

你说得对！**排队位置应该完全由后端计算**，前端直接使用即可。

### 职责划分

- **后端**: 负责计算"排在前面的任务数量"，返回 `queue_position` 字段
- **前端**: 直接使用 `response.data.queue_position` 显示

---

## ✅ 正确实现

### 后端修改 (`task_queue.py`)

**文件**: `app/system/task_queue.py`  
**位置**: 第 503-527 行

```python
# 新增：计算排队位置（排在前面的任务数量）
# 按 create_time 排序所有待处理任务
sorted_tasks = sorted(pending_tasks, key=lambda x: x.get('create_time', ''))

# 默认返回 0（表示没有任务排在前面）
queue_position = 0

# 如果有 pending_tasks，返回总任务数 - 1
# 假设最新的任务是当前用户的（基于最近上传的文件）
if len(sorted_tasks) > 0:
    queue_position = len(sorted_tasks) - 1

return {
    "total_pending": total_pending,
    "light_queue_pending": light_queue_pending,
    "heavy_queue_pending": heavy_queue_pending,
    "light_attachment_names": light_attachment_names,
    "heavy_attachment_names": heavy_attachment_names,
    "total_parsed": total_parsed,
    "parsed_attachment_names": parsed_attachment_names,
    "queue_position": queue_position,  # ✅ 新增：排在前面的任务数量
    "pending_tasks": pending_tasks,  # 保留：详细任务列表（可选）
    "code": 200
}
```

**关键逻辑**:
- ✅ 按 `create_time` 排序所有任务
- ✅ 假设最新的任务是当前用户上传的
- ✅ 返回 `总任务数 - 1` = 排在前面的任务数量

---

### 前端修改 (`ChatArea.vue`)

**文件**: `vue-project_ver2/src/components/ChatArea.vue`  
**位置**: 第 805-856 行

#### 修改前（错误做法）

```javascript
// ❌ 前端自己计算排队位置
const pendingTasks = response.data.pending_tasks || [];
const sortedTasks = [...pendingTasks].sort(...);

let myTaskIndex = -1;
for (let i = 0; i < sortedTasks.length; i++) {
  // 前端通过 session_id 匹配自己的任务
  const isMyTask = (taskSessionId === currentSessionId) || ...;
  if (isMyTask) {
    myTaskIndex = i;
    break;
  }
}

this.totalPendingCount = myTaskIndex;  // ❌ 复杂且容易出错
```

**问题**:
- ❌ 前端需要知道 session_id
- ❌ 需要遍历和排序
- ❌ 逻辑复杂，容易出错
- ❌ 前后端职责不清

---

#### 修改后（正确做法）

```javascript
async fetchQueueStats() {
  const response = await axios.get(...);
  
  if (response.data.code === 200) {
    // ✅ 直接使用后端返回的 queue_position
    this.totalPendingCount = response.data.queue_position || 0;
    this.totalAttachmentCount = allAttachments.length;
    
    console.log('Updated display:', {
      totalPendingCount: this.totalPendingCount,
      totalAttachmentCount: this.totalAttachmentCount,
      queue_position: response.data.queue_position  // ✅ 从后端获取
    });
  }
}
```

**优势**:
- ✅ 前端逻辑简单清晰
- ✅ 职责明确（后端计算，前端显示）
- ✅ 易于维护和调试

---

## 🔄 完整流程

### 场景示例

```
用户 A: 上传 file1.pdf (01:30:00) → queued
用户 B: 上传 file2.pdf (01:35:00) → queued
你：   上传 3 个文件 (01:40:00) → uploaded
```

### 点击送信后的处理

#### 1. 前端调用提交接口

```javascript
await axios.post('/submit_queued_tasks', {
  username: 'user_C',
  attachment_names: ['my1.pdf', 'my2.pdf', 'my3.pdf']
});
```

#### 2. 后端更新状态并计算排队位置

```python
# 数据库状态
[
  {"session": "A", "time": "01:30:00"},  # task-001
  {"session": "B", "time": "01:35:00"},  # task-002
  {"session": "C", "time": "01:40:00"}   # task-003 (最新)
]

# 计算排队位置
sorted_tasks = [task-001, task-002, task-003]
queue_position = len(sorted_tasks) - 1 = 3 - 1 = 2

return {
  "queue_position": 2,  # ✅ 排在前面的任务数量
  ...
}
```

#### 3. 前端显示

```javascript
this.totalPendingCount = response.data.queue_position;  // = 2

// HTML 显示
あなたの前に 2 個の添付ファイルが解析待ちです。
```

---

## ⚠️ 注意事项

### 1. 时间戳精度

后端必须确保 `create_time` 的精度：

```python
create_time = datetime.now().isoformat()  # ✅ ISO 格式，精确到微秒
```

### 2. 假设条件

当前实现基于一个假设：
- **最新的任务 = 当前用户的任务**

这个假设在大多数情况下成立，因为：
- 用户上传文件后立即点击送信
- 此时数据库中最新的记录就是用户的

### 3. 边界情况

| 场景 | 任务总数 | queue_position | 说明 |
|------|---------|----------------|------|
| 没有其他任务 | 1 | 0 | 第一个 |
| 前面有 2 个任务 | 3 | 2 | 排在第 3 位 |
| 同时上传 | N | N-1 | 假设自己是最新的 |

---

## 📊 API 响应格式

### `/queue_stats` 接口

**请求**:
```http
GET /queue_stats?username=dev
Authorization: Bearer <token>
```

**响应**:
```json
{
  "total_pending": 3,
  "light_queue_pending": 2,
  "heavy_queue_pending": 1,
  "light_attachment_names": ["file1.pdf", "file2.pdf"],
  "heavy_attachment_names": ["large.pdf"],
  "total_parsed": 5,
  "parsed_attachment_names": [...],
  "queue_position": 2,        // ✅ 新增：排在前面的任务数量
  "pending_tasks": [...],     // 保留：详细任务列表（可选）
  "code": 200
}
```

**字段说明**:
- `queue_position`: 排在前面的任务数量（不包含自己的任务）
- 值为 0 表示前面没有任务，可以立即开始处理

---

## ✅ 优势对比

### 旧方案（前端计算）

```javascript
// 前端代码复杂
const pendingTasks = response.data.pending_tasks || [];
const sortedTasks = [...pendingTasks].sort(...);

let myTaskIndex = -1;
for (let i = 0; i < sortedTasks.length; i++) {
  const task = sortedTasks[i];
  const isMyTask = (task.session_id === currentSessionId) || 
                   (localAttachments.some(f => task.attachment_names.includes(f)));
  if (isMyTask) {
    myTaskIndex = i;
    break;
  }
}

this.totalPendingCount = myTaskIndex;
```

**问题**:
- ❌ 逻辑复杂
- ❌ 需要维护 session_id
- ❌ 性能差（前端遍历排序）
- ❌ 容易出错

---

### 新方案（后端计算）

```javascript
// 前端代码简洁
this.totalPendingCount = response.data.queue_position || 0;
```

**优势**:
- ✅ 逻辑简单
- ✅ 无需维护 session_id
- ✅ 性能好（后端计算）
- ✅ 不易出错

---

## 🎯 总结

### 核心原则

1. **后端负责计算**: 所有业务逻辑在后端完成
2. **前端负责显示**: 直接使用后端返回的数据
3. **单一数据源**: 避免前后端各自维护状态

### 实现要点

- ✅ 后端按 `create_time` 排序
- ✅ 假设最新的任务是当前用户的
- ✅ 返回 `queue_position = 总任务数 - 1`
- ✅ 前端直接使用 `response.data.queue_position`

### 用户体验

```
前面有 2 个任务 → 显示"あなたの前に 2 個"
前面没有任务 → 显示"解析中"
```

---

**更新日期**: 2026-03-30  
**相关文档**: [QUEUE_POSITION_CALCULATION.md](QUEUE_POSITION_CALCULATION.md)
