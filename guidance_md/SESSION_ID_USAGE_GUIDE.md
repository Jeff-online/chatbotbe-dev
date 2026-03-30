# Session ID 在队列系统中的使用指南

## 📌 Session ID 的重要性

`session_id` 是连接文件、队列记录和用户会话的关键标识符，它确保了：

1. **会话关联**: 将文件解析结果正确关联到特定会话
2. **历史追溯**: 可以按会话查询历史文件和消息
3. **状态管理**: 切换会话时能正确显示对应的文件列表
4. **数据统计**: 按会话统计文件使用情况和 token 消耗

---

## 🔄 Session ID 的完整流转

### 1. 文件上传阶段

**前端调用**:
```javascript
const formData = new FormData();
formData.append('username', 'dev');
formData.append('file', file);
formData.append('session_id', this.sessionId);  // ✅ 关键：传递 session_id

await axios.post('/upload_file', formData, { ... });
```

**后端处理** (`app/system/homepage.py` 第 320-386 行):
```python
def post(self):
    args_parser = FileParser()
    args = args_parser.parser.parse_args()
    
    username = args.get("username")
    file = args.get("file")
    session_id = args.get("session_id")  # ✅ 获取 session_id
    
    # 创建数据库记录
    queue_state_id = QueueState.create(
        username=username,
        queue_name=queue_name,
        message=message_json,
        message_id=temp_message_id,
        pop_receipt=None,
        status="uploaded",
        account_name=None,
        session_id=session_id  # ✅ 保存 session_id
    )
```

**数据库记录格式**:
```json
{
    "id": "abc-123-def",
    "type": "queue_state",
    "username": "dev",
    "session_id": "session_456",  // ✅ 关键：会话 ID
    "queue_name": "light-queue",
    "message": "{...}",
    "status": "uploaded",
    "create_time": "2026-03-30T01:37:43.148643"
}
```

---

### 2. 提交任务阶段

**前端调用**:
```javascript
await axios.post('/submit_queued_tasks', {
    username: username,
    session_id: this.sessionId,  // ✅ 传递当前会话 ID
    attachment_names: this.attachmentNames
});
```

**后端处理** (`app/system/task_queue.py` 第 836-951 行):
```python
class SubmitQueuedTasks(GlobalResource):
    def post(self):
        data = request.get_json()
        username = data.get('username')
        session_id = data.get('session_id')  # ✅ 获取 session_id
        attachment_names = data.get('attachment_names', [])
        
        # 查找 uploaded 状态的记录
        query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username AND c.status = 'uploaded'"
        items = list(container.query_items(query=query, ...))
        
        for item in items:
            # 更新状态时会保留原有的 session_id
            item['status'] = 'queued'
            container.upsert_item(item)
```

**注意**: 
- 提交操作会保留原有的 `session_id`
- 不需要额外处理，因为上传时已经保存

---

### 3. 任务处理阶段

**带锁处理** (`app/system/task_queue.py` 第 601-660 行):
```python
@staticmethod
def process_with_lock(queue_name, message_id, processor_func, *args, **kwargs):
    try:
        # 获取锁
        lock_acquired = QueueConcurrencyLock.acquire_lock(queue_name, message_id)
        
        # 更新为 processing
        QueueState.update_status_by_message_id(message_id, "processing")
        
        # 执行处理
        result = processor_func(*args, **kwargs)
        
        # 更新为 parsed
        QueueState.update_status_by_message_id(message_id, "parsed")
        
        return result
    finally:
        if lock_acquired:
            QueueConcurrencyLock.release_lock(queue_name, message_id)
```

**说明**: 
- 状态更新时不会修改 `session_id`
- `session_id` 始终保持不变

---

### 4. 查询会话相关记录

#### 按会话查询队列状态

```http
GET /queue_state?username=dev&session_id=session_456
```

**查询示例**:
```python
query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username AND c.session_id = @session_id"
params = [
    {"name": "@username", "value": "dev"},
    {"name": "@session_id", "value": "session_456"}
]
items = list(container.query_items(query=query, parameters=params))
```

**返回示例**:
```json
{
    "queue_state": [
        {
            "id": "abc-123",
            "session_id": "session_456",
            "queue_name": "light-queue",
            "status": "parsed",
            "attachment_names": ["file1.pdf"]
        }
    ]
}
```

#### 按会话统计

```python
# 统计某个会话的文件数量
query = """
    SELECT VALUE {
        total: COUNT(1),
        uploaded: SUM(CASE WHEN c.status = 'uploaded' THEN 1 ELSE 0 END),
        queued: SUM(CASE WHEN c.status = 'queued' THEN 1 ELSE 0 END),
        processing: SUM(CASE WHEN c.status = 'processing' THEN 1 ELSE 0 END),
        parsed: SUM(CASE WHEN c.status = 'parsed' THEN 1 ELSE 0 END)
    }
    FROM c
    WHERE c.type = 'queue_state' 
      AND c.username = @username 
      AND c.session_id = @session_id
"""
```

---

## 💡 实际应用场景

### 场景 1: 切换会话时清理文件列表

```javascript
// 用户切换到新会话
this.sessionId = newSessionId;

// 只显示当前会话的文件
const currentSessionFiles = this.uploadedFiles.filter(
    file => file.session_id === this.sessionId
);

// 或者重新查询
const response = await axios.get(`/queue_state?username=dev&session_id=${this.sessionId}`);
this.attachmentNames = response.data.queue_state.map(item => 
    JSON.parse(item.message).attachment_names[0]
);
```

### 场景 2: 显示会话历史

```javascript
// 加载会话时，同时加载相关文件
async loadSession(sessionId) {
    const [sessionResponse, filesResponse] = await Promise.all([
        axios.get(`/session_management?session_id=${sessionId}`),
        axios.get(`/queue_state?username=dev&session_id=${sessionId}`)
    ]);
    
    // 显示会话消息
    this.messages = sessionResponse.data.messages;
    
    // 显示相关文件
    this.attachmentNames = filesResponse.data.queue_state
        .filter(item => item.status === 'parsed')
        .map(item => JSON.parse(item.message).attachment_names[0]);
}
```

### 场景 3: 删除会话时级联清理

```python
def delete_session_with_files(username, session_id):
    """删除会话时，同时删除相关文件记录"""
    
    # 1. 删除会话
    container.delete_item(item=session_id, partition_key=session_id)
    
    # 2. 查询该会话的所有文件记录
    query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.session_id = @session_id"
    params = [{"name": "@session_id", "value": session_id}]
    items = list(container.query_items(query=query, parameters=params))
    
    # 3. 删除所有文件记录
    for item in items:
        # 如果是 queued 状态，先清理 Azure Queue
        if item.get('status') == 'queued':
            queue_client.delete_message(item['message_id'], item['pop_receipt'])
        
        # 删除 Cosmos DB 记录
        container.delete_item(item=item['id'], partition_key=item['id'])
    
    # 4. 删除 Blob 存储
    blobs = list(container_client.list_blobs(name_starts_with=f"{username}/"))
    for blob in blobs:
        container_client.delete_blob(blob.name)
```

---

## ⚠️ 注意事项

### 1. Session ID 可能为空

在某些情况下，`session_id` 可能为空：
- 用户上传文件但还没有创建会话
- 测试环境或调试模式

**处理方式**:
```python
# 允许 session_id 为空
item = {
    "id": doc_id,
    "username": username,
    # session_id 可选
}
if session_id:
    item["session_id"] = session_id
```

### 2. 跨会话文件共享

如果需要在多个会话间共享文件：
- 方案 1: 复制文件记录，每个会话一份
- 方案 2: 使用公共会话 ID（如 `"shared"`）
- 方案 3: 不关联 session_id（设为 null）

### 3. 性能优化

对于大量数据的查询：
```python
# ✅ 好的做法：使用索引
query = """
    SELECT * FROM c 
    WHERE c.type = 'queue_state' 
      AND c.username = @username 
      AND c.session_id = @session_id
    ORDER BY c.create_time DESC
"""

# ❌ 不推荐：全表扫描
query = "SELECT * FROM c WHERE c.username = @username"
# 然后手动过滤 session_id
```

---

## 🔍 数据库索引建议

为了提高按 `session_id` 查询的性能，建议创建复合索引：

```json
{
    "indexingMode": "consistent",
    "includedPaths": [
        {
            "path": "/*",
            "indexes": [
                { "kind": "Hash", "dataType": "String" },
                { "kind": "Range", "dataType": "String" }
            ]
        }
    ],
    "compositeIndexes": [
        [
            { "path": "/username", "order": "ascending" },
            { "path": "/session_id", "order": "ascending" }
        ],
        [
            { "path": "/type", "order": "ascending" },
            { "path": "/session_id", "order": "ascending" }
        ]
    ]
}
```

---

## 📊 API 端点中的 Session ID

| 端点 | Method | Session ID 处理 |
|------|--------|----------------|
| `/upload_file` | POST | 必须从表单获取并保存 |
| `/submit_queued_tasks` | POST | 可选参数，用于过滤 |
| `/queue_state` | GET | 可选参数，用于查询 |
| `/queue_stats` | GET | 可选参数，用于统计 |
| `/delete_uploaded_record` | DELETE | 可选参数，用于精确删除 |

---

## ✅ 最佳实践总结

1. **上传时必须保存 session_id**
   ```python
   session_id = args.get("session_id")
   QueueState.create(..., session_id=session_id)
   ```

2. **查询时推荐使用 session_id 过滤**
   ```python
   query += " AND c.session_id = @session_id"
   ```

3. **前端应该始终传递 session_id**
   ```javascript
   formData.append('session_id', this.sessionId);
   ```

4. **删除会话时级联清理文件记录**
   ```python
   delete_session_with_files(username, session_id)
   ```

5. **使用复合索引优化查询性能**
   ```
   CREATE INDEX idx_username_session ON queue_state(username, session_id)
   ```

---

**更新日期**: 2026-03-30  
**相关文档**: [UPLOAD_AND_SUBMIT_GUIDE.md](UPLOAD_AND_SUBMIT_GUIDE.md)
