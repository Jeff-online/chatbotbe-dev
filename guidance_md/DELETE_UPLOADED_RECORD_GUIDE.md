# 取消文件上传的数据库记录删除指南

## 📌 问题描述

当用户在前端取消文件上传（或删除已上传的文件）时，必须在数据库层面也删除对应的队列记录，否则会导致：

1. **数据不一致**: Blob 存储中文件已删除，但数据库中仍有记录
2. **排队统计错误**: 已删除的文件仍显示在排队列表中
3. **资源浪费**: 无效的数据库记录占用存储空间

---

## ✅ 解决方案

### 1. 增强 `delete_by_filename` 方法

**位置**: `app/system/task_queue.py`  
**修改内容**: 查询所有状态的记录，而不仅仅是 `queued`

**修改前**:
```python
query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username AND c.status = 'queued'"
```

**修改后**:
```python
query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username"
# 查询所有状态：uploaded, queued, processing, parsed, failed
```

**关键改进**:
- ✅ 支持所有状态的记录删除
- ✅ 只对 `queued` 状态从 Azure Queue 删除消息
- ✅ 对所有状态都从 Cosmos DB 删除记录
- ✅ 返回删除数量统计

---

### 2. 新增专用接口 `/delete_uploaded_record`

**用途**: 专门用于删除 `uploaded` 状态的记录（用户取消上传）

**端点**: `DELETE /delete_uploaded_record`

**请求参数**:
```json
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

**代码位置**: `app/system/task_queue.py` 第 708-783 行

---

## 🔄 完整流程

### 场景 1: 用户上传文件后取消

```
用户上传文件
    ↓
创建 uploaded 状态记录
    ↓
用户点击"×"取消上传
    ↓
前端调用 DELETE /delete_uploaded_record
    ↓
后端删除 uploaded 状态记录
    ↓
✅ 数据库记录已清理
```

### 场景 2: 删除已提交的文件

```
用户删除已提交的文件
    ↓
前端调用 DELETE /upload_file?filename=test.pdf
    ↓
后端调用 QueueState.delete_by_filename()
    ↓
删除所有状态的记录（uploaded/queued/processing/parsed/failed）
    ↓
如果是 queued 状态，同时从 Azure Queue 删除消息
    ↓
✅ 数据库和队列记录都已清理
```

---

## 💻 前端集成

### ChatArea.vue 实现示例

#### 1. 删除单个文件方法

```javascript
methods: {
    // 删除单个文件（点击文件旁边的"×"按钮）
    async deleteFile(filename) {
        const token = sessionStorage.getItem('token');
        const username = sessionStorage.getItem('username');
        
        try {
            // 1. 先删除数据库记录（针对 uploaded 状态）
            await axios.delete(
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
            
            // 2. 再删除 Blob 存储
            await axios.delete(
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

#### 2. HTML 模板

```vue
<template>
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
</template>
```

#### 3. CSS 样式

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

## 📊 API 端点一览

### 1. 删除 uploaded 状态记录（新增）

```http
DELETE /delete_uploaded_record
Content-Type: application/json

请求体:
{
    "username": "dev",
    "filename": "test.pdf"
}

响应:
{
    "message": "Deleted 1 uploaded record(s) for file test.pdf",
    "deleted_count": 1,
    "code": 200
}
```

### 2. 删除文件（原有）

```http
DELETE /upload_file?username=dev&filename=test.pdf

响应:
{
    "msg": "test.pdf ファイルの削除が成功しました",
    "code": 200
}
```

**注意**: 此接口会自动调用 `QueueState.delete_by_filename()` 删除所有状态的记录

---

## 🔍 后端逻辑详解

### `QueueState.delete_by_filename()` 方法

**查询范围**: 所有状态（uploaded, queued, processing, parsed, failed）

**处理逻辑**:
```python
for item in items:
    if filename in attachments:
        # 直接从 Cosmos DB 删除记录（所有状态）
        current_app.container_task_queue.delete_item(item=doc_id, partition_key=doc_id)
```

**日志输出**:
```
🔵 Attempting to delete queue record and message for user: dev, file: test.pdf
🔍 Found 3 records (all statuses) for user dev
   Checking record abc-123 (status: uploaded), attachments: [test.pdf]
✅ Deleted queue state record abc-123 (status: uploaded) for file test.pdf
✅ Successfully deleted 1 record(s) for file test.pdf
```

---

### `DeleteUploadedRecord.delete()` 方法

**查询范围**: 仅 `uploaded` 状态

**处理逻辑**:
```python
query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username AND c.status = 'uploaded'"

for item in items:
    if filename in attachments:
        # 直接从 Cosmos DB 删除记录
        current_app.container_task_queue.delete_item(item=doc_id, partition_key=doc_id)
```

**使用场景**: 
- 用户上传文件后立即取消
- 文件还未提交到队列（状态为 uploaded）
- 不需要清理 Azure Queue（因为还没发送）

---

## ⚠️ 注意事项

### 1. 删除顺序

**推荐顺序**:
1. 先删除数据库记录
2. 再删除 Blob 存储

**原因**: 
- 数据库删除失败可以重试
- Blob 删除后无法恢复
- 保持最终一致性

### 2. 错误处理

```javascript
try {
    // 尝试删除数据库记录
    await deleteDatabaseRecord(filename);
    
    // 尝试删除 Blob
    await deleteBlob(filename);
    
} catch (error) {
    // 如果删除失败，记录日志但不阻止用户操作
    console.error('Delete error:', error);
    
    // 可以显示友好提示
    Notification.warning({
        title: '警告',
        message: '文件已删除，但数据库记录可能需要稍后清理'
    });
}
```

### 3. 并发删除

如果多个用户可能同时删除同一个文件：

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

## 🧪 测试建议

### 1. 单元测试

```python
def test_delete_uploaded_record():
    """测试删除 uploaded 状态记录"""
    # 创建测试记录
    doc_id = QueueState.create(
        username="test_user",
        queue_name="light-queue",
        message="test",
        message_id="temp-id",
        status="uploaded"
    )
    
    # 删除记录
    QueueState.delete_by_filename("test_user", "test.pdf")
    
    # 验证已删除
    try:
        container.read_item(item=doc_id, partition_key=doc_id)
        assert False, "Record should be deleted"
    except CosmosHttpResponseError:
        pass  # 预期异常
```

### 2. 集成测试

```bash
# 1. 上传文件
curl -X POST http://localhost:5000/upload_file \
  -F "username=dev" \
  -F "file=@test.pdf"

# 2. 删除 uploaded 记录
curl -X DELETE http://localhost:5000/delete_uploaded_record \
  -H "Content-Type: application/json" \
  -d '{"username":"dev","filename":"test.pdf"}'

# 3. 验证记录已删除
curl -X GET "http://localhost:5000/queue_state?username=dev&status=uploaded"
```

---

## 📝 配置文件更新

在 `vue-project_ver2/src/config.js` 中添加新端点：

```javascript
export default {
    apiBaseUrl: 'https://your-api-base-url',
    endpoints: {
        upload_file: '/upload_file',
        delete_uploaded_record: '/delete_uploaded_record',  // 新增
        submit_queued_tasks: '/submit_queued_tasks',
        queue_state: '/queue_state',
        queue_stats: '/queue_stats'
    }
};
```

---

## ✅ 总结

### 核心改进

1. **增强 `delete_by_filename`**: 支持所有状态的记录删除
2. **新增专用接口**: `/delete_uploaded_record` 用于取消上传
3. **前端集成示例**: 完整的删除逻辑实现

### 使用场景对比

| 接口 | 适用场景 | 删除范围 |
|------|----------|----------|
| `DELETE /delete_uploaded_record` | 取消刚上传的文件 | 仅 uploaded 状态 |
| `DELETE /upload_file` | 删除任何状态的文件 | 所有状态 + Azure Queue |

### 关键特性

- ✅ 自动清理数据库记录
- ✅ 支持所有状态（uploaded/queued/processing/parsed/failed）
- ✅ 只对 queued 状态清理 Azure Queue
- ✅ 返回删除数量统计
- ✅ 完整的错误处理和日志记录

---

**更新日期**: 2026-03-30  
**相关文档**: [UPLOAD_AND_SUBMIT_GUIDE.md](UPLOAD_AND_SUBMIT_GUIDE.md)
