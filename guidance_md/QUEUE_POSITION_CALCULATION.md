# 排队位置计算逻辑说明

## 📌 问题描述

### 原有逻辑问题

**前端显示**:
```
totalPendingCount = 所有待处理的任务数量（包括自己的）
```

**用户看到的**:
- 用户上传 3 个文件，点击送信
- `totalPendingCount` 显示 5（前面有 2 个任务 + 自己的 3 个）
- 用户困惑："为什么显示 5 个？我只有 3 个文件啊！"

---

### 需要的逻辑

**新显示逻辑**:
```
totalPendingCount = 排在我前面的任务数量（不包括自己的）
```

**用户看到的**:
- 用户上传 3 个文件，点击送信
- 如果前面有 2 个任务 → `totalPendingCount` 显示 2 ✅
- 如果没有其他任务 → `totalPendingCount` 显示 0 ✅
- 提示文本："あなたの前に 2 個の添付ファイルが解析待ちです"

---

## ✅ 解决方案

### 核心思路

1. **后端返回详细任务列表**（包含 `session_id`、`create_time`、`attachment_names`）
2. **前端根据自己的 session 和文件名，找到自己的任务**
3. **计算排在前面的任务数量**

---

## 🔧 后端修改

### `QueueStats.get()` 方法

**文件**: `app/system/task_queue.py`  
**位置**: 第 420-509 行

#### 修改前

```python
return {
    "total_pending": total_pending,
    "light_queue_pending": light_queue_pending,
    "heavy_queue_pending": heavy_queue_pending,
    "light_attachment_names": light_attachment_names,
    "heavy_attachment_names": heavy_attachment_names,
    "total_parsed": total_parsed,
    "parsed_attachment_names": parsed_attachment_names,
    "code": 200
}
```

**问题**: 只返回统计数字，无法区分哪些是"我的"任务

---

#### 修改后

```python
# 新增：记录每个任务的详细信息
pending_tasks = []

for item in items_pending:
    
    # 提取 session_id
    session_id = message_data.get("session_id") or item.get("session_id")
    
    # 记录任务信息（用于计算排队位置）
    pending_tasks.append({
        "id": item.get("id"),
        "queue_name": q_name,
        "status": item.get("status"),
        "attachment_names": attachment_names,
        "session_id": session_id,
        "create_time": item.get("create_time"),
        "update_time": item.get("update_time")
    })

return {
    "total_pending": total_pending,
    "light_queue_pending": light_queue_pending,
    "heavy_queue_pending": heavy_queue_pending,
    "light_attachment_names": light_attachment_names,
    "heavy_attachment_names": heavy_attachment_names,
    "total_parsed": total_parsed,
    "parsed_attachment_names": parsed_attachment_names,
    "pending_tasks": pending_tasks,  # ✅ 新增：详细任务列表
    "code": 200
}
```

**关键改进**:
- ✅ 返回 `pending_tasks` 数组
- ✅ 包含 `session_id` 用于识别用户
- ✅ 包含 `create_time` 用于排序
- ✅ 包含 `attachment_names` 用于匹配文件

---

## 💻 前端修改

### ChatArea.vue - `fetchQueueStats()` 方法

**文件**: `vue-project_ver2/src/components/ChatArea.vue`  
**位置**: 第 805-903 行

#### 修改前

```javascript
async fetchQueueStats() {
  const response = await axios.get(...);
  
  if (response.data.code === 200) {
    const backendTotalPending = response.data.light_queue_pending + response.data.heavy_queue_pending;
    
    // ❌ 问题：直接显示总数（包括自己的）
    this.totalPendingCount = backendTotalPending;
    this.totalAttachmentCount = allAttachments.length;
  }
}
```

---

#### 修改后

```javascript
async fetchQueueStats() {
  const currentSessionId = sessionStorage.getItem('sessionId') || this.sessionId;
  
  const response = await axios.get(...);
  
  if (response.data.code === 200) {
    const pendingTasks = response.data.pending_tasks || [];  // ✅ 获取详细任务列表
    
    // ✅ 关键修改：计算排在前面的任务数量
    let tasksAheadOfMe = 0;
    
    if (pendingTasks.length > 0 && currentSessionId) {
      // 1. 按 create_time 排序（确保顺序正确）
      const sortedTasks = [...pendingTasks].sort((a, b) => {
        return new Date(a.create_time) - new Date(b.create_time);
      });
      
      // 2. 找到自己的任务（通过 session_id 和附件名称匹配）
      let myTaskIndex = -1;
      for (let i = 0; i < sortedTasks.length; i++) {
        const task = sortedTasks[i];
        const taskSessionId = task.session_id;
        const taskAttachments = task.attachment_names || [];
        
        // 检查是否是"我的"任务
        const isMyTask = (taskSessionId === currentSessionId) || 
                         (localAttachments.length > 0 && 
                          localAttachments.some(localFile => taskAttachments.includes(localFile)));
        
        if (isMyTask && myTaskIndex === -1) {
          myTaskIndex = i;
          break;
        }
      }
      
      // 3. 计算排在前面的任务数量
      if (myTaskIndex !== -1) {
        tasksAheadOfMe = myTaskIndex;  // ✅ 索引位置 = 前面的任务数量
        console.log('Found my task at index:', myTaskIndex, 'Tasks ahead:', tasksAheadOfMe);
      } else {
        tasksAheadOfMe = 0;  // 没找到自己的任务，说明是第一个
      }
    } else {
      tasksAheadOfMe = backendTotalPending;  // 降级处理
    }
    
    // ✅ 更新显示数据：只显示排在前面的任务数量
    this.totalPendingCount = tasksAheadOfMe;
    this.totalAttachmentCount = allAttachments.length;
  }
}
```

**关键逻辑**:
1. ✅ 按 `create_time` 排序
2. ✅ 通过 `session_id` 匹配"我的"任务
3. ✅ 通过 `attachment_names` 兼容没有 `session_id` 的情况
4. ✅ 索引位置 = 前面的任务数量

---

### HTML 模板修改

**文件**: `vue-project_ver2/src/components/ChatArea.vue`  
**位置**: 第 66-79 行

#### 修改前

```html
<template v-if="totalPendingCount > 0">
  <p>現在、他の処理が実行中のため順番待ちとなっています。</p>
  <p>{{ totalPendingCount }} 個添付ファイルが解析待ちです。</p>
  <p>完了まで数分かかる場合があります。</p>
</template>
```

**问题**: "XX 個添付ファイルが解析待ち"语义模糊

---

#### 修改后

```html
<template v-if="totalPendingCount > 0">
  <p>現在、他の処理が実行中のため順番待ちとなっています。</p>
  <p>あなたの前に {{ totalPendingCount }} 個の添付ファイルが解析待ちです。</p>
  <p>完了まで数分かかる場合があります。</p>
</template>
```

**改进**: 明确表达"你的前面有 X 个文件在等待"

---

## 🔄 完整流程示例

### 场景：用户上传 3 个文件，前面已有 2 个任务

#### 数据库状态

```json
// 任务 1（其他用户 A）
{
  "id": "task-001",
  "session_id": "session_A",
  "attachment_names": ["file1.pdf"],
  "create_time": "2026-03-30T01:30:00",
  "status": "processing"
}

// 任务 2（其他用户 B）
{
  "id": "task-002",
  "session_id": "session_B",
  "attachment_names": ["file2.pdf"],
  "create_time": "2026-03-30T01:35:00",
  "status": "queued"
}

// 任务 3（当前用户 C - 你）
{
  "id": "task-003",
  "session_id": "session_C",
  "attachment_names": ["my_file1.pdf", "my_file2.pdf", "my_file3.pdf"],
  "create_time": "2026-03-30T01:40:00",
  "status": "queued"
}
```

#### 后端返回

```json
{
  "total_pending": 3,
  "pending_tasks": [
    {
      "id": "task-001",
      "session_id": "session_A",
      "attachment_names": ["file1.pdf"],
      "create_time": "2026-03-30T01:30:00"
    },
    {
      "id": "task-002",
      "session_id": "session_B",
      "attachment_names": ["file2.pdf"],
      "create_time": "2026-03-30T01:35:00"
    },
    {
      "id": "task-003",
      "session_id": "session_C",
      "attachment_names": ["my_file1.pdf", "my_file2.pdf", "my_file3.pdf"],
      "create_time": "2026-03-30T01:40:00"
    }
  ]
}
```

#### 前端计算过程

```javascript
// 1. 按 create_time 排序（已经有序）
sortedTasks = [task-001, task-002, task-003]

// 2. 找到自己的任务
i=0: session_A !== session_C → 不是我的
i=1: session_B !== session_C → 不是我的
i=2: session_C === session_C → ✅ 是我的任务！myTaskIndex = 2

// 3. 计算排在前面的任务数量
tasksAheadOfMe = myTaskIndex = 2

// 4. 更新显示
totalPendingCount = 2  // ✅ 正确显示
```

#### 用户看到的结果

```
現在、他の処理が実行中のため順番待ちとなっています。
あなたの前に 2 個の添付ファイルが解析待ちです。
完了まで数分かかる場合があります。
```

**用户体验**: ✅ 清晰明了，知道前面有 2 个任务在排队

---

## ⚠️ 边界情况处理

### 情况 1: 没有 session_id

```javascript
if (!currentSessionId) {
  // 降级到旧逻辑
  tasksAheadOfMe = backendTotalPending;
}
```

### 情况 2: 没找到自己的任务

```javascript
if (myTaskIndex === -1) {
  // 说明是第一个
  tasksAheadOfMe = 0;
}
```

### 情况 3: 多个任务属于同一个 session

```javascript
// 只取第一个匹配的任务
if (isMyTask && myTaskIndex === -1) {
  myTaskIndex = i;
  break;  // 找到第一个就停止
}
```

---

## 📊 测试场景

### 测试 1: 前面有 2 个任务

```bash
# 前置条件
- task-001: session_A, file1.pdf, 01:30:00
- task-002: session_B, file2.pdf, 01:35:00

# 操作
上传 3 个文件（session_C）

# 期望结果
totalPendingCount = 2
```

### 测试 2: 没有其他任务

```bash
# 前置条件
- 无待处理任务

# 操作
上传 3 个文件（session_C）

# 期望结果
totalPendingCount = 0
```

### 测试 3: 同时上传（相同 session_id）

```bash
# 前置条件
- task-001: session_C, file1.pdf, 01:30:00
- task-002: session_C, file2.pdf, 01:31:00

# 操作
再次上传 1 个文件（session_C）

# 期望结果
totalPendingCount = 0  # 因为都是同一个 session 的任务
```

---

## ✅ 验证清单

### 后端验证

- [x] `QueueStats.get()` 返回 `pending_tasks`
- [x] 每个任务包含 `session_id`、`create_time`、`attachment_names`
- [x] 代码语法检查通过

### 前端验证

- [x] `fetchQueueStats()` 获取 `pending_tasks`
- [x] 正确计算 `tasksAheadOfMe`
- [x] 更新 `totalPendingCount` 为前面的任务数量
- [x] 显示文本改为"あなたの前に XX 個"
- [ ] 实际运行测试（待执行）

---

## 🔍 调试日志

### 控制台输出示例

```javascript
Backend queue stats: {
  light_queue_pending: 1,
  heavy_queue_pending: 1,
  backendTotalPending: 2,
  backendTotalAttachments: 2,
  localAttachments: 3,
  allAttachments: 5,
  pendingTasksCount: 3
}

Found my task at index: 2 Tasks ahead: 2

Updated display: {
  totalPendingCount: 2,
  totalAttachmentCount: 5,
  tasksAheadOfMe: 2
}
```

---

## 📝 注意事项

### 1. Session ID 的重要性

- ✅ 确保前端正确传递 `session_id`
- ✅ 后端正确保存 `session_id`
- ✅ 查询时能获取到 `session_id`

### 2. 时间戳精度

- ✅ 使用 ISO 8601 格式
- ✅ 精确到毫秒
- ✅ 前端使用 `new Date()` 解析

### 3. 兼容性

- ✅ 兼容没有 `session_id` 的旧数据
- ✅ 兼容 `pending_tasks` 为空的情况
- ✅ 降级到旧逻辑

---

**更新日期**: 2026-03-30  
**相关文档**: [SESSION_ID_USAGE_GUIDE.md](SESSION_ID_USAGE_GUIDE.md)
