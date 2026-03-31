# 队列并发控制实现总结

## 📋 需求确认

用户要求实现以下功能：
1. **文件上传时**：创建数据库记录，但不立即发送到队列
2. **点击送信时**：才真正触发排队机制
3. **并发控制**：heavy-queue 最多 1 个任务，light-queue 最多 2 个任务同时处理

## ✅ 已完成的工作

### 1. 后端修改

#### (1) 文件上传接口 (`app/system/homepage.py`)

**修改内容**:
- 文件上传时只创建 Cosmos DB 记录，状态设为 `uploaded`
- 返回 `queue_name` 和 `queue_state_id` 给前端

**关键变更**:
```python
# 原来：直接发送到队列
status = "queued"
send_to_queue()

# 现在：仅创建记录，等待提交
status = "uploaded"  # 新状态
```

#### (2) 新增提交接口 (`app/system/task_queue.py`)

**新增类**: `SubmitQueuedTasks`

**端点**: `POST /submit_queued_tasks`

**功能**:
- 查找所有 `status='uploaded'` 的记录
- 更新状态为 `queued`
- 更新消息内容（移除 'waiting for submit' 标记）

**请求参数**:
```json
{
    "username": "dev",
    "attachment_names": ["file1.pdf", "file2.pdf"]
}
```

#### (3) 新增处理接口 (`app/system/task_queue.py`)

**新增类**: `ProcessTaskWithLock`

**端点**: `POST /process_task_with_lock`

**功能**:
- 使用 `TaskQueue.process_with_lock()` 方法
- 自动获取和释放并发锁
- 执行实际的业务处理

#### (4) 并发控制机制 (`app/system/task_queue.py`)

**新增类**: `QueueConcurrencyLock`

**特性**:
- ✅ heavy-queue: 最大 1 个并发
- ✅ light-queue: 最大 2 个并发
- ✅ 使用 Cosmos DB 存储锁状态
- ✅ 自动超时检测（10 分钟）
- ✅ 自动重试机制（3 秒间隔）
- ✅ 分布式友好

**核心方法**:
- `acquire_lock(queue_name, message_id)`: 获取锁
- `release_lock(queue_name, message_id)`: 释放锁

**封装方法**:
- `TaskQueue.process_with_lock()`: 完整的带锁处理流程

### 2. 数据库记录状态

**新增状态**: `uploaded` 和 `parsed`

**完整状态流转**:
```
uploaded → queued → processing → parsed
                       ↘
                        failed
```

**各状态含义**:
- `uploaded`: 文件已上传，等待用户点击“送信”
- `queued`: 已提交到队列，等待处理
- `processing`: 正在处理中（已获得锁）
- `parsed`: **文件已解析完成，AI 已返回响应** ⭐
- `failed`: 处理失败

**重要说明**:
- 使用 `parsed` 而不是 `completed` 是为了明确表示文件解析已完成
- 当 AI 返回响应时，自动将状态更新为 `parsed`
- `parsed` 状态的文件不会再参与排队统计
- 前端可以根据 `parsed` 状态判断是否可以显示 AI 响应

### 3. 测试脚本

#### (1) `test_submit_flow.py`

**功能**: 测试完整的上传和提交流程

**测试步骤**:
1. 模拟文件上传，创建 `uploaded` 状态记录
2. 查询所有 `uploaded` 状态记录
3. 模拟点击送信，更新为 `queued` 状态
4. 验证状态转换是否正确

#### (2) `verify_queue_concurrency.py`

**功能**: 验证并发控制机制

**测试场景**:
1. 基础锁获取和释放功能
2. Heavy-queue 并发限制（3 个任务，最大并发=1）
3. Light-queue 并发限制（5 个任务，最大并发=2）
4. 混合队列测试（heavy 和 light 同时运行）

### 4. 文档

#### (1) `UPLOAD_AND_SUBMIT_GUIDE.md`

**内容**:
- 完整的流程图
- API 接口说明
- 前端集成示例
- 状态流转说明
- 测试步骤

#### (2) `QUEUE_CONCURRENCY_GUIDE.md`

**内容**:
- 并发控制机制详解
- 使用示例
- 监控和调试指南

## 🔄 完整流程

### 用户上传文件阶段

```
用户选择文件
    ↓
前端调用 /upload_file
    ↓
后端：
  1. 上传到 Azure Blob
  2. 计算 token，决定队列
  3. 创建 DB 记录 (status='uploaded')
    ↓
返回 queue_name 和 queue_state_id
    ↓
前端显示文件在附件列表
```

### 用户点击送信阶段

```
用户点击"送信"按钮
    ↓
前端调用 /submit_queued_tasks
    ↓
后端：
  1. 查找 uploaded 状态记录
  2. 更新 status='queued'
  3. 更新 message 内容
    ↓
前端开始轮询队列状态
    ↓
后台处理器：
  1. 查询 queued 状态记录
  2. 调用 /process_task_with_lock
  3. 获取并发锁
  4. 执行业务逻辑
  5. 释放锁
    ↓
前端显示处理结果
```

## 📊 数据库记录格式

### 上传时的记录格式

```json
{
    "id": "abc-123-def",
    "type": "queue_state",
    "username": "dev",
    "queue_name": "light-queue",
    "message": "{\"account_name\": null, \"queue_name\": \"light-queue\", \"user-name\": \"dev\", \"create_time\": \"2026-03-30T01:37:42.825728\", \"status\": \"uploaded\", \"message\": \"File uploaded: test.pdf (waiting for submit)\", \"attachment_names\": [\"test.pdf\"], \"session_id\": \"session_123\"}",
    "message_id": "temp-uuid-123",  // 临时 ID
    "pop_receipt": null,             // 空
    "status": "uploaded",            // 关键：uploaded 状态
    "account_name": null,
    "create_time": "2026-03-30T01:37:43.148643",
    "session_id": "session_123"
}
```

### 提交后的记录格式

```json
{
    "id": "abc-123-def",
    "type": "queue_state",
    "username": "dev",
    "queue_name": "light-queue",
    "message": "{\"account_name\": null, \"queue_name\": \"light-queue\", \"user-name\": \"dev\", \"create_time\": \"2026-03-30T01:37:42.825728\", \"status\": \"queued\", \"message\": \"File uploaded: test.pdf\", \"attachment_names\": [\"test.pdf\"], \"session_id\": \"session_123\"}",
    "message_id": "99d17866-69fe-4bad-9fdf-ba2edf81c66b",  // 真实 ID
    "pop_receipt": "AgAAAAMAAAAAAAAAm86LzeW/3AE=",       // 真实 receipt
    "status": "queued",              // 已提交到队列
    "account_name": null,
    "create_time": "2026-03-30T01:37:43.148643",
    "update_time": "2026-03-30T01:40:00.000000",  // 更新时间
    "session_id": "session_123"
}
```

## 🎯 关键特性

### 1. 两阶段提交

- **阶段 1**: 文件上传（`uploaded` 状态）
- **阶段 2**: 队列提交（`queued` 状态）

### 2. 并发控制

- **Heavy-queue**: 串行处理（最大并发=1）
- **Light-queue**: 双路并行（最大并发=2）

### 3. 自动恢复

- 超时锁自动释放（10 分钟）
- 失败任务自动标记（`failed` 状态）

### 4. 分布式支持

- 锁状态存储在 Cosmos DB
- 多实例部署安全

## 📝 前端集成要点

### 需要修改的地方

1. **文件上传方法**: 保持不变
2. **送信按钮**: 增加提交到队列的调用
3. **队列轮询**: 增加对 `uploaded` 状态的轮询

### API 端点配置

在 `vue-project_ver2/src/config.js` 中添加：

```javascript
endpoints: {
    upload_file: '/upload_file',
    submit_queued_tasks: '/submit_queued_tasks',
    process_task_with_lock: '/process_task_with_lock',
    queue_state: '/queue_state',
    queue_stats: '/queue_stats'
}
```

## ⚠️ 注意事项

1. **数据一致性**:
   - 上传失败时需要清理
   - 提交失败时需要回滚

2. **用户体验**:
   - 上传成功后显示"准备发送"状态
   - 送信后显示队列等待对话框

3. **错误处理**:
   - 网络错误的重试机制
   - 友好的错误提示

## 🧪 测试建议

### 单元测试

```bash
# 测试上传和提交流程
python test_submit_flow.py

# 测试并发控制
python verify_queue_concurrency.py
```

### 集成测试

1. 上传多个文件
2. 点击送信
3. 观察队列状态变化
4. 验证并发限制是否生效

## 📚 相关文档

- [上传和提交指南](UPLOAD_AND_SUBMIT_GUIDE.md)
- [并发控制指南](QUEUE_CONCURRENCY_GUIDE.md)
- [原始需求参考](call_openai_with_global_lock_gpt5 实现)

## ✨ 下一步工作

### 前端修改（ChatArea.vue）

需要在 `sendMessage()` 方法中：

1. 调用 `/submit_queued_tasks` 接口
2. 传递 `attachment_names` 列表
3. 处理返回结果

### 业务逻辑集成

在 `ProcessTaskWithLock` 的 `actual_processor()` 函数中：

1. 替换示例代码为实际业务逻辑
2. 调用 OpenAI API 或其他处理
3. 保存处理结果

---

**实现完成时间**: 2026-03-30  
**实现者**: AI Assistant  
**状态**: ✅ 完成
