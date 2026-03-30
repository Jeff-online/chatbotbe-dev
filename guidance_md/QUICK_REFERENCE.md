# 🚀 快速参考指南

## API 端点一览

### 1. 文件上传
```
POST /upload_file
Content-Type: multipart/form-data

参数:
- username (表单字段)
- file (文件)
- session_id (可选)

返回:
{
    "filename": "test.pdf",
    "queue_name": "light-queue",
    "queue_state_id": "abc-123",
    "code": 200
}
```

### 2. 提交任务到队列
```
POST /submit_queued_tasks
Content-Type: application/json

请求体:
{
    "username": "dev",
    "attachment_names": ["file1.pdf", "file2.pdf"]
}

返回:
{
    "submitted_count": 2,
    "results": [...],
    "code": 200
}
```

### 3. 带锁处理任务
```
POST /process_task_with_lock
Content-Type: application/json

请求体:
{
    "username": "dev",
    "queue_name": "light-queue",
    "message_id": "msg-123"
}

返回:
{
    "success": true,
    "result": {...},
    "code": 200
}
```

### 4. 查询队列状态
```
GET /queue_state?username=dev&status=queued

返回:
{
    "queue_state": [
        {
            "message_id": "msg-123",
            "queue_name": "light-queue",
            "status": "processing"
        }
    ],
    "code": 200
}
```

### 5. 查询队列统计
```
GET /queue_stats?username=dev

返回:
{
    "total_pending": 3,
    "light_queue_pending": 2,
    "heavy_queue_pending": 1,
    "code": 200
}
```

---

## 数据库记录状态

| 状态 | 说明 | 触发时机 |
|------|------|----------|
| `uploaded` | 文件已上传，等待提交 | 调用 `/upload_file` |
| `queued` | 已提交到队列 | 调用 `/submit_queued_tasks` |
| `processing` | 正在处理 | 获得并发锁 |
| `parsed` | **文件已解析完成，AI 已返回响应** | AI 返回响应时 |
| `failed` | 处理失败 | 处理出错 |

**注意**: 
- `parsed` 表示文件解析完成，不再参与排队统计
- 前端可根据 `parsed` 状态显示“解析完成”

---

## 并发限制

| 队列 | 最大并发 | 判定标准 |
|------|----------|----------|
| heavy-queue | 1 | tokens > 50000 |
| light-queue | 2 | tokens ≤ 50000 |

---

## 前端调用示例

### 上传文件
```javascript
const formData = new FormData();
formData.append('file', file);
formData.append('username', 'dev');

const response = await axios.post(
    '/upload_file',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } }
);
```

### 提交任务
```javascript
const response = await axios.post(
    '/submit_queued_tasks',
    {
        username: 'dev',
        attachment_names: ['file1.pdf', 'file2.pdf']
    }
);
```

### 轮询状态
```javascript
const pollInterval = setInterval(async () => {
    const response = await axios.get(
        `/queue_stats?username=${username}`
    );
    
    if (response.data.total_pending === 0) {
        clearInterval(pollInterval);
    }
}, 3000);
```

---

## 测试命令

### 运行提交流程测试
```bash
python test_submit_flow.py
```

### 运行并发控制测试
```bash
python verify_queue_concurrency.py
```

---

## 日志关键字

| 关键字 | 说明 |
|--------|------|
| 🔵 | 开始操作 |
| ✅ | 操作成功 |
| ⚠️ | 警告信息 |
| ❌ | 操作失败 |
| ⏳ | 等待中 |
| ⚙️ | 处理中 |

---

## 故障排查

### 问题：上传后找不到记录

**检查**:
1. 数据库连接是否正常
2. `container_task_queue` 是否创建
3. 查看日志中的错误信息

### 问题：提交后没有处理

**检查**:
1. Azure Queue 连接字符串是否正确
2. 后台处理器是否运行
3. 并发锁是否被占用

### 问题：并发控制不生效

**检查**:
1. `queue_concurrency_lock` 容器是否存在
2. 锁文档是否正确创建
3. 超时时间设置是否合理

---

## 相关文件

- **后端实现**:
  - `app/system/homepage.py` - 文件上传
  - `app/system/task_queue.py` - 队列管理

- **前端组件**:
  - `vue-project_ver2/src/components/ChatArea.vue`

- **测试脚本**:
  - `test_submit_flow.py` - 流程测试
  - `verify_queue_concurrency.py` - 并发测试

- **文档**:
  - `UPLOAD_AND_SUBMIT_GUIDE.md` - 详细指南
  - `QUEUE_CONCURRENCY_GUIDE.md` - 并发控制
  - `IMPLEMENTATION_SUMMARY.md` - 实现总结

---

## 常用查询

### 查询所有 uploaded 状态的记录
```sql
SELECT * FROM c 
WHERE c.type = 'queue_state' 
  AND c.username = @username 
  AND c.status = 'uploaded'
```

### 查询所有 processing 状态的记录
```sql
SELECT * FROM c 
WHERE c.type = 'queue_state' 
  AND c.status = 'processing'
```

### 按队列类型统计
```sql
SELECT 
    VALUE {
        queue: c.queue_name,
        count: COUNT(1)
    }
FROM c
WHERE c.type = 'queue_state'
  AND c.status = 'queued'
GROUP BY c.queue_name
```
