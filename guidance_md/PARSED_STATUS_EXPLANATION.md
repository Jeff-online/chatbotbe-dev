# Parsed 状态说明

## 📌 为什么需要 `parsed` 状态？

在文件上传和队列处理的完整流程中，我们需要明确区分两个概念：

1. **任务处理完成** (`completed`) - 后台处理逻辑执行完毕
2. **文件解析完成** (`parsed`) - AI 已经返回响应，文件内容已被解析

### 问题场景

如果使用 `completed` 状态，会有以下混淆：
- 用户看到"完成"但不知道是"处理完成"还是"解析完成"
- 前端无法判断是否可以显示 AI 响应
- 排队统计可能包含已经完成解析的文件

### 解决方案

使用 `parsed` 状态明确表示：
- ✅ **AI 已返回响应**
- ✅ **文件内容已解析完毕**
- ✅ **可以显示解析结果**
- ✅ **不再参与排队统计**

---

## 🔄 状态流转对比

### 旧方案（使用 completed）

```
uploaded → queued → processing → completed
```

**问题**: "completed" 含义模糊，无法区分是"处理完成"还是"解析完成"

### 新方案（使用 parsed）

```
uploaded → queued → processing → parsed
                       ↘
                        failed
```

**优势**: 
- `parsed` 明确表示"文件已解析"
- 与 AI 响应直接关联
- 前端可以准确判断显示时机

---

## 💡 实际应用场景

### 1. 前端显示逻辑

```javascript
// 轮询队列状态
const response = await axios.get('/queue_stats?username=dev');

const { total_pending, total_parsed } = response.data;

if (total_parsed > 0) {
    // ✅ 有文件已解析完成，可以显示 AI 响应
    showAIResponse();
}

if (total_pending > 0) {
    // ⏳ 仍有文件在排队或处理中
    showQueueMessage(`${total_pending} 个文件正在处理...`);
}
```

### 2. 后端处理逻辑

```python
@staticmethod
def process_with_lock(queue_name, message_id, processor_func, *args, **kwargs):
    lock_acquired = False
    
    try:
        # 1. 获取锁
        lock_acquired = QueueConcurrencyLock.acquire_lock(queue_name, message_id)
        
        # 2. 更新为 processing
        QueueState.update_status_by_message_id(message_id, "processing")
        
        # 3. 执行处理（调用 AI API）
        result = processor_func(*args, **kwargs)
        
        # 4. ✅ 关键：AI 返回响应后，更新为 parsed
        QueueState.update_status_by_message_id(message_id, "parsed")
        
        return result
        
    except Exception as e:
        # 5. 失败时更新为 failed
        QueueState.update_status_by_message_id(message_id, "failed")
        raise e
        
    finally:
        # 6. 释放锁
        if lock_acquired:
            QueueConcurrencyLock.release_lock(queue_name, message_id)
```

### 3. 数据库查询优化

```sql
-- 查询待处理文件（不包括已解析的）
SELECT * FROM c 
WHERE c.type = 'queue_state' 
  AND c.username = @username 
  AND c.status IN ('queued', 'processing')

-- 查询已解析文件
SELECT * FROM c 
WHERE c.type = 'queue_state' 
  AND c.status = 'parsed'
```

---

## 📊 QueueStats 接口返回

### 修改前

```json
{
    "total_pending": 5,
    "light_queue_pending": 3,
    "heavy_queue_pending": 2,
    "code": 200
}
```

**问题**: 无法知道有多少文件已经解析完成

### 修改后

```json
{
    "total_pending": 2,              // ⏳ 待处理（queued + processing）
    "light_queue_pending": 1,        // ⏳ light-queue 待处理
    "heavy_queue_pending": 1,        // ⏳ heavy-queue 待处理
    "total_parsed": 3,               // ✅ 已解析完成
    "parsed_attachment_names": [     // ✅ 已解析的文件列表
        "file1.pdf",
        "file2.pdf",
        "file3.pdf"
    ],
    "code": 200
}
```

**优势**: 
- 前端可以清楚知道进度
- 可以显示"已完成 X/总数 Y"
- 可以根据 `parsed_attachment_names` 高亮已解析的文件

---

## 🎯 前端集成建议

### ChatArea.vue 修改示例

```javascript
data() {
    return {
        // ... existing data ...
        parsedFiles: [],           // 新增：已解析文件列表
        isProcessing: false,       // 新增：是否正在处理
        parsedCount: 0             // 新增：已解析数量
    };
},

methods: {
    async startQueuePolling() {
        const pollInterval = setInterval(async () => {
            try {
                const token = sessionStorage.getItem('token');
                const username = sessionStorage.getItem('username');
                
                const response = await axios.get(
                    `${config.apiBaseUrl}${config.endpoints.queue_stats}`,
                    {
                        params: { username: username },
                        headers: { Authorization: `Bearer ${token}` }
                    }
                );
                
                if (response.data.code === 200) {
                    const { total_pending, total_parsed, parsed_attachment_names } = response.data;
                    
                    // 更新已解析文件列表
                    this.parsedFiles = parsed_attachment_names || [];
                    this.parsedCount = total_parsed || 0;
                    
                    // 如果所有文件都解析完成
                    if (total_pending === 0 && total_parsed > 0) {
                        clearInterval(pollInterval);
                        this.showQueueDialog = false;
                        this.isProcessing = false;
                        
                        // 显示完成提示
                        Notification.success({
                            title: '解析完了！',
                            message: `共解析 ${total_parsed} 个文件`,
                            duration: 3000
                        });
                        
                        // 清空附件列表（可选）
                        // this.attachmentNames = [];
                    }
                    
                    // 更新队列对话框显示
                    if (total_pending > 0) {
                        this.totalPendingCount = total_pending;
                        this.isProcessing = true;
                    }
                }
            } catch (error) {
                console.error('Polling error:', error);
            }
        }, 3000);
        
        return pollInterval;
    }
}
```

---

## 🔍 日志示例

### 成功的处理流程日志

```
🔵 开始为任务 msg-123 获取 light-queue 锁...
✅ 成功获取 light-queue 锁槽位，当前活跃数：1/2
✅ 任务 msg-123 成功获取锁，开始处理...
✅ Updated queue state to 'processing' for message_id: msg-123
⚙️ Processing: user=dev, attachments=['test.pdf']
✅ 任务 msg-123 处理完成
✅ Updated queue state to 'parsed' for message_id: msg-123
✅ 任务 msg-123 已释放锁
✅ 成功释放 light-queue 锁槽位，当前活跃数：0/2
```

**关键点**:
- `processing` → 开始处理
- `parsed` → AI 返回响应

---

## 📝 API 使用示例

### 查询队列状态（包含 parsed）

```bash
# 查询所有状态
curl -X GET "http://localhost:5000/queue_state?username=dev"

# 只查询待处理（queued + processing）
curl -X GET "http://localhost:5000/queue_state?username=dev&status=queued"

# 只查询已解析
curl -X GET "http://localhost:5000/queue_state?username=dev&status=parsed"
```

### 查询统计信息

```bash
curl -X GET "http://localhost:5000/queue_stats?username=dev"
```

返回：
```json
{
    "total_pending": 2,
    "total_parsed": 3,
    "parsed_attachment_names": ["file1.pdf", "file2.pdf", "file3.pdf"],
    "code": 200
}
```

---

## ✅ 总结

### 使用 `parsed` 的优势

1. **语义清晰**: 明确表示"文件已解析"而非泛义的"完成"
2. **前端友好**: 可以准确判断何时显示 AI 响应
3. **统计准确**: `parsed` 文件不参与排队统计
4. **用户体验**: 可以显示详细的进度（已解析 X/总数 Y）

### 关键变更点

- ✅ `TaskQueue.process_with_lock()` 方法：完成时设置为 `parsed`
- ✅ `QueueStats.get()` 方法：分别统计 `pending` 和 `parsed`
- ✅ 前端轮询逻辑：根据 `parsed` 状态判断完成

### 注意事项

1. **不要使用 `completed`**: 避免语义混淆
2. **及时清理**: 定期归档或删除 `parsed` 状态的记录
3. **错误处理**: `failed` 状态仍需保留，用于标记处理失败的文件

---

**更新时间**: 2026-03-30  
**更新内容**: 引入 `parsed` 状态替代 `completed`
