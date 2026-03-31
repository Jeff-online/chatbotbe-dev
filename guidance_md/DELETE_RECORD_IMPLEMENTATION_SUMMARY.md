# 取消文件上传记录删除功能 - 实现总结

## 🎯 问题发现

用户在前端取消文件上传时，存在**数据不一致**问题：

```
用户上传文件
    ↓
创建 uploaded 状态记录 ✅
    ↓
用户点击"×"取消
    ↓
仅删除了 Blob 存储 ❌
数据库记录仍存在 ❌
```

**后果**:
- 数据库中残留无效记录
- 排队统计包含已删除的文件
- 前端显示错误的文件列表

---

## ✅ 解决方案

### 1. 增强 `QueueState.delete_by_filename()` 方法

**修改位置**: `app/system/task_queue.py` 第 272-333 行

**关键改进**:

#### 修改前
```python
query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username AND c.status = 'queued'"
# 只查询 queued 状态 ❌
```

#### 修改后
```python
query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username"
# 查询所有状态 ✅
# uploaded, queued, processing, parsed, failed
```

**处理逻辑优化**:
```python
for item in items:
    if filename in attachments:
        # 直接删除 Cosmos DB 记录（所有状态）
        current_app.container_task_queue.delete_item(item=doc_id, partition_key=doc_id)
```

**日志增强**:
```
🔵 Attempting to delete queue record and message for user: dev, file: test.pdf
🔍 Found 3 records (all statuses) for user dev  ← 显示所有状态
   Checking record abc-123 (status: uploaded), attachments: [test.pdf]  ← 显示状态
✅ Deleted queue state record abc-123 (status: uploaded) for file test.pdf
✅ Successfully deleted 1 record(s) for file test.pdf  ← 返回统计
```

---

### 2. 新增专用接口 `/delete_uploaded_record`

**位置**: `app/system/task_queue.py` 第 708-783 行

**用途**: 专门用于取消刚上传的文件（状态为 `uploaded`）

**请求示例**:
```http
DELETE /delete_uploaded_record
Content-Type: application/json

{
    "username": "dev",
    "filename": "test.pdf"
}
```

**返回示例**:
```json
{
    "message": "Deleted 1 uploaded record(s) for file test.pdf",
    "deleted_count": 1,
    "code": 200
}
```

**优势**:
- ✅ 轻量级操作（只查询 uploaded 状态）
- ✅ 不需要清理 Azure Queue（还没发送）
- ✅ 快速响应用户取消操作

---

## 🔄 完整流程对比

### 场景 1: 取消刚上传的文件（推荐用新接口）

```
用户上传文件
    ↓
创建 uploaded 状态记录
    ↓
用户点击"×"取消
    ↓
前端调用 DELETE /delete_uploaded_record ✅
    ↓
后端删除 uploaded 状态记录
    ↓
前端删除 Blob 存储
    ↓
✅ 数据库和存储都已清理
```

### 场景 2: 删除已提交的文件（使用原有接口）

```
用户删除已提交的文件
    ↓
前端调用 DELETE /upload_file
    ↓
后端调用 QueueState.delete_by_filename()
    ↓
删除所有状态的记录
    ↓
如果是 queued，同时清理 Azure Queue
    ↓
✅ 完全清理
```

---

## 💻 前端集成代码

### ChatArea.vue 需要添加的方法

```javascript
methods: {
    // 删除单个文件（点击文件旁边的"×"按钮）
    async deleteFile(filename) {
        const token = sessionStorage.getItem('token');
        const username = sessionStorage.getItem('username');
        
        try {
            // 1. 先删除数据库记录（针对 uploaded 状态）
            const dbResponse = await axios.delete(
                `${config.apiBaseUrl}${config.endpoints.delete_uploaded_record}`,
                {
                    data: {
                        username: username,
                        filename: filename
                    },
                    headers: {
                        Authorization: `Bearer ${token}`,
                        'Content-Type': 'application/json'
                    }
                }
            );
            
            console.log('Database record deleted:', dbResponse.data);
            
            // 2. 再删除 Blob 存储
            const blobResponse = await axios.delete(
                `${config.apiBaseUrl}${config.endpoints.upload_file}`,
                {
                    headers: {
                        Authorization: `Bearer ${token}`
                    },
                    params: {
                        username: username,
                        filename: filename
                    }
                }
            );
            
            // 3. 更新前端附件列表
            const index = this.attachmentNames.indexOf(filename);
            if (index > -1) {
                this.attachmentNames.splice(index, 1);
            }
            
            // 4. 更新文件大小统计
            const fileObj = this.uploadedFiles.find(f => f.name === filename);
            if (fileObj) {
                this.totalSize -= fileObj.size;
                const index = this.uploadedFiles.indexOf(fileObj);
                if (index > -1) {
                    this.uploadedFiles.splice(index, 1);
                }
            }
            
            Notification.success({
                title: '削除完了',
                message: `${filename} を削除しました`,
                duration: 2000
            });
            
        } catch (error) {
            console.error('Delete file error:', error);
            Notification.error({
                title: 'エラー',
                message: 'ファイルの削除に失敗しました'
            });
        }
    }
}
```

### HTML 模板

```vue
<div class="file-display-area">
    <!-- 提示文件 -->
    <div v-if="promptName" class="file-item">
        <span class="file-name">{{ promptName }}</span>
        <button class="delete-btn" @click="deleteFile(promptName)">&times;</button>
    </div>
    
    <!-- 附件文件 -->
    <div v-for="name in attachmentNames" :key="name" class="file-item">
        <span class="file-name">{{ name }}</span>
        <button class="delete-btn" @click="deleteFile(name)">&times;</button>
    </div>
</div>
```

### CSS 样式

```css
.file-item {
    display: flex;
    align-items: center;
    padding: 5px 10px;
    background-color: #f5f5f5;
    border-radius: 4px;
    margin: 5px 0;
}

.file-name {
    flex: 1;
    font-size: 14px;
    color: #333;
}

.delete-btn {
    background: none;
    border: none;
    color: #999;
    cursor: pointer;
    font-size: 18px;
    padding: 0 5px;
    transition: color 0.2s;
}

.delete-btn:hover {
    color: #ff4444;
}
```

---

## 📊 API 端点完整列表

| 端点 | 方法 | 用途 | 删除范围 |
|------|------|------|----------|
| `/delete_uploaded_record` | DELETE | 取消刚上传的文件 | 仅 uploaded 状态 |
| `/upload_file` | DELETE | 删除任何状态的文件 | 所有状态 + Azure Queue |

---

## 🔍 后端处理逻辑详解

### `QueueState.delete_by_filename()` 流程图

```
开始
    ↓
查询所有状态的记录
    ↓
遍历每条记录
    ↓
检查附件列表是否包含文件名
    ↓
是 → 状态是 queued?
    ↓
是 → 从 Azure Queue 删除消息
    ↓
从 Cosmos DB 删除记录
    ↓
统计删除数量
    ↓
返回结果
```

### 状态处理

```python
# 所有状态都只从 Cosmos DB 删除
if status == 'queued':
    # 从 Cosmos DB 删除
    delete_from_cosmos()
elif status == 'uploaded':
    # 从 Cosmos DB 删除
    delete_from_cosmos()
elif status == 'processing':
    # 等待处理完成后再清理
    # 或强制清理（根据业务需求）
    delete_from_cosmos()
elif status == 'parsed':
    # 清理历史记录
    delete_from_cosmos()
elif status == 'failed':
    # 清理失败记录
    delete_from_cosmos()
```

---

## ⚠️ 重要注意事项

### 1. 删除顺序

**推荐**: 先数据库，后存储

```javascript
// ✅ 好的做法
await deleteDatabaseRecord(filename);
await deleteBlob(filename);

// ❌ 不推荐
await deleteBlob(filename);
await deleteDatabaseRecord(filename);
```

**原因**:
- 数据库删除失败可以重试
- Blob 删除后无法恢复
- 保持最终一致性

### 2. 错误处理策略

```javascript
try {
    await deleteDatabaseRecord(filename);
    await deleteBlob(filename);
} catch (error) {
    // 记录错误但不阻止用户
    console.error('Delete error:', error);
    
    // 显示友好提示
    Notification.warning({
        title: '警告',
        message: '文件已删除，但部分记录可能需要稍后清理'
    });
}
```

### 3. 并发删除处理

```python
try:
    container.delete_item(item=doc_id, partition_key=doc_id)
except CosmosHttpResponseError as e:
    if e.status_code == 404:
        # 记录已被其他请求删除
        logger.warning(f"Record {doc_id} already deleted")
    else:
        raise
```

---

## 🧪 测试验证

### 测试脚本

```python
"""测试取消文件上传的记录删除"""
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

# 初始化连接
client = CosmosClient('https://ailab-db-dev.documents.azure.com:443/', credential)
database = client.get_database_client('chatbot_test1')
container = database.get_container_client('task_queue')

# 1. 创建 uploaded 状态记录
doc_id = str(uuid.uuid4())
item = {
    "id": doc_id,
    "type": "queue_state",
    "username": "test_user",
    "queue_name": "light-queue",
    "message": json.dumps({
        "attachment_names": ["test.pdf"]
    }),
    "status": "uploaded"
}
container.create_item(body=item)
print(f"✅ Created test record: {doc_id}")

# 2. 删除记录
QueueState.delete_by_filename("test_user", "test.pdf")
print(f"✅ Deleted record")

# 3. 验证已删除
try:
    container.read_item(item=doc_id, partition_key=doc_id)
    print("❌ Record still exists!")
except CosmosHttpResponseError:
    print("✅ Record successfully deleted")
```

### 手动测试步骤

1. **上传文件**
   ```bash
   curl -X POST http://localhost:5000/upload_file \
     -F "username=dev" \
     -F "file=@test.pdf"
   ```

2. **查询记录**
   ```bash
   curl -X GET "http://localhost:5000/queue_state?username=dev&status=uploaded"
   ```

3. **删除 uploaded 记录**
   ```bash
   curl -X DELETE http://localhost:5000/delete_uploaded_record \
     -H "Content-Type: application/json" \
     -d '{"username":"dev","filename":"test.pdf"}'
   ```

4. **验证删除**
   ```bash
   curl -X GET "http://localhost:5000/queue_state?username=dev&status=uploaded"
   # 应该返回空列表
   ```

---

## 📝 配置文件更新

在 `vue-project_ver2/src/config.js` 中添加：

```javascript
export default {
    apiBaseUrl: 'https://your-api-base-url',
    endpoints: {
        upload_file: '/upload_file',
        delete_uploaded_record: '/delete_uploaded_record',  // ← 新增
        submit_queued_tasks: '/submit_queued_tasks',
        queue_state: '/queue_state',
        queue_stats: '/queue_stats'
    }
};
```

---

## ✅ 验证清单

- [x] `QueueState.delete_by_filename()` 支持所有状态
- [x] 新增 `/delete_uploaded_record` 接口
- [x] 前端集成代码示例
- [x] 完整的错误处理
- [x] 详细的日志输出
- [x] 文档和测试脚本
- [ ] 前端实际集成（待实施）
- [ ] 单元测试（待执行）
- [ ] 集成测试（待执行）

---

## 🔗 相关文档

- [删除指南](DELETE_UPLOADED_RECORD_GUIDE.md) - 完整的使用指南
- [上传和提交指南](UPLOAD_AND_SUBMIT_GUIDE.md) - 整体流程说明
- [Parsed 状态说明](PARSED_STATUS_EXPLANATION.md) - 状态流转详解

---

**实现时间**: 2026-03-30  
**实现者**: AI Assistant  
**状态**: ✅ 完成
