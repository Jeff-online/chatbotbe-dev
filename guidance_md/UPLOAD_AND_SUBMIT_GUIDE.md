# 文件上传和队列提交流程说明

## 概述

实现了两阶段的文件处理流程：
1. **文件上传阶段**：上传文件到 Azure Blob，创建数据库记录（状态：`uploaded`）
2. **队列提交阶段**：用户点击"送信"按钮时，才真正发送到 Azure Queue 并触发排队机制

## 流程图

```
用户上传文件
    ↓
1. 上传到 Azure Blob Storage
    ↓
2. 创建 Cosmos DB 记录 (status='uploaded')
    ↓
   [等待用户点击"送信"]
    ↓
3. 更新记录状态 (status='queued')
    ↓
4. 发送到 Azure Queue
    ↓
5. 后台处理任务（带并发控制）
    ↓
6. 处理完成 (status='completed')
```

## 后端 API

### 1. 文件上传接口

**端点**: `POST /upload_file`

**功能**: 
- 上传文件到 Azure Blob Storage
- 计算 token 数量，决定使用哪个队列（Token > 30,000 则进入 heavy-queue，否则 light-queue）
- 创建 Cosmos DB 记录，状态为 `uploaded`

**请求参数**:
```python
{
    "username": "dev",
    "file": <FileStorage>,
    "session_id": "session_123"  # 可选
}
```

**返回示例**:
```json
{
    "message": "File 'test.pdf' uploaded successfully",
    "file_path": "dev/test.pdf",
    "filename": "test.pdf",
    "queue_name": "light-queue",
    "queue_state_id": "abc-123-def",
    "code": 200
}
```

**关键代码位置**: `app/system/homepage.py` 第 320-398 行

### 2. 提交任务到队列接口

**端点**: `POST /submit_queued_tasks`

**功能**:
- 查找所有状态为 `uploaded` 的记录
- 更新状态为 `queued`
- 发送到 Azure Queue
- 更新数据库记录的 message_id 和 pop_receipt

**请求参数**:
```json
{
    "username": "dev",
    "session_id": "session_123",
    "attachment_names": ["file1.pdf", "file2.pdf"]
}
```

**返回示例**:
```json
{
    "message": "Submitted 2 task(s) for processing",
    "code": 200,
    "submitted_count": 2,
    "results": [
        {
            "filename": ["file1.pdf"],
            "queue_name": "light-queue",
            "status": "queued"
        },
        {
            "filename": ["file2.pdf"],
            "queue_name": "heavy-queue",
            "status": "queued"
        }
    ]
}
```

**关键代码位置**: `app/system/task_queue.py` 第 708-804 行

### 3. 带锁的任务处理接口

**端点**: `POST /process_task_with_lock`

**功能**:
- 从队列中获取任务
- 使用并发控制锁处理任务
- heavy-queue: 最多 1 个并发
- light-queue: 最多 2 个并发

**请求参数**:
```json
{
    "username": "dev",
    "queue_name": "light-queue",
    "message_id": "msg-123"
}
```

**关键代码位置**: `app/system/task_queue.py` 第 807-861 行

## 前端集成示例

### ChatArea.vue 修改建议

#### 1. 文件上传方法

```javascript
async uploadFile(event) {
  const files = event.target.files;
  if (!files || files.length === 0) return;

  const token = sessionStorage.getItem('token');
  const username = sessionStorage.getItem('username');
  
  for (let i = 0; i < files.length; i++) {
    const formData = new FormData();
    formData.append('file', files[i]);
    formData.append('username', username);
    if (this.sessionId) {
      formData.append('session_id', this.sessionId);
    }

    try {
      this.isUploading = true;
      
      // 调用上传接口
      const response = await axios.post(
        `${config.apiBaseUrl}${config.endpoints.upload_file}`,
        formData,
        {
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'multipart/form-data'
          }
        }
      );

      if (response.data.code === 200) {
        // 添加到附件列表
        if (!this.attachmentNames.includes(files[i].name)) {
          this.attachmentNames.push(files[i].name);
          this.uploadedFiles.push(files[i]);
        }
        
        Notification.success({
          title: '添付ファイル',
          message: 'アップロード成功',
          duration: 3000
        });
      }
    } catch (error) {
      Notification.error({
        title: 'ファイルのアップロード',
        message: 'ファイルのアップロードに失敗しました'
      });
    } finally {
      this.isUploading = false;
    }
  }
  
  event.target.value = '';
}
```

#### 2. 送信按钮处理方法

```javascript
async sendMessage() {
  this.showInitialPrompt = false;
  const message = this.message.trim();
  if (!message && this.attachmentNames.length === 0) return;

  // 禁用发送按钮
  this.isSending = true;
  
  // 显示队列对话框
  this.showQueueDialog = true;
  this.totalPendingCount = this.attachmentNames.length;
  this.totalAttachmentCount = this.attachmentNames.length;
  
  try {
    const token = sessionStorage.getItem('token');
    const username = sessionStorage.getItem('username');
    
    // 第一步：提交附件到队列
    if (this.attachmentNames.length > 0) {
      const submitResponse = await axios.post(
        `${config.apiBaseUrl}${config.endpoints.submit_queued_tasks}`,
        {
          username: username,
          session_id: this.sessionId,
          attachment_names: this.attachmentNames
        },
        {
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json'
          }
        }
      );
      
      console.log('Submitted tasks:', submitResponse.data);
    }
    
    // 第二步：发送消息文本（如果有）
    if (message) {
      // ... 原有的消息发送逻辑 ...
    }
    
    // 第三步：轮询队列状态
    this.startQueuePolling();
    
  } catch (error) {
    console.error('Send message error:', error);
    Notification.error({
      title: 'エラー',
      message: 'メッセージ送信に失敗しました'
    });
  } finally {
    this.isSending = false;
  }
}
```

#### 3. 队列状态轮询

```javascript
startQueuePolling() {
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
        this.totalPendingCount = response.data.total_pending;
        
        // 如果所有任务都完成了，停止轮询
        if (this.totalPendingCount === 0) {
          clearInterval(pollInterval);
          this.showQueueDialog = false;
          
          // 清空附件列表
          this.attachmentNames = [];
          this.uploadedFiles = [];
        }
      }
    } catch (error) {
      console.error('Polling error:', error);
    }
  }, 3000);  // 每 3 秒轮询一次
  
  return pollInterval;
}
```

## 数据库记录状态流转

### 状态说明

1. **uploaded**: 文件已上传，等待用户点击“送信”
2. **queued**: 已提交到队列，等待处理
3. **processing**: 正在处理中（已获得锁）
4. **parsed**: **文件已解析完成，AI 已返回响应** （新增）
5. **failed**: 处理失败

### 状态转换

```
uploaded → queued → processing → parsed
                       ↘
                        failed
```

**重要说明**:
- 使用 `parsed` 而不是 `completed` 是为了明确表示**文件解析已完成**
- 当 AI 返回响应时，表示当前 session 的文件已经解析完毕
- `parsed` 状态的文件不会再参与排队统计
- 前端可以根据 `parsed` 状态显示“解析完成”提示

## 并发控制机制

### Heavy-queue
- **最大并发数**: 1
- **适用场景**: 大文件、复杂处理
- **判定标准**: token 数量 > 30000

### Light-queue
- **最大并发数**: 2
- **适用场景**: 小文件、简单处理
- **判定标准**: token 数量 ≤ 30000

### 锁机制

- 使用 Cosmos DB 存储锁状态
- 容器名：`queue_concurrency_lock`
- 锁文档 ID: `heavy_queue_lock` 和 `light_queue_lock`
- 超时时间：10 分钟
- 重试间隔：3 秒

## 测试

### 运行测试脚本

```bash
# 测试完整的上传和提交流程
python test_submit_flow.py

# 测试并发控制机制
python verify_queue_concurrency.py
```

### 手动测试步骤

1. **上传文件**
   ```bash
   curl -X POST http://localhost:5000/upload_file \
     -F "username=dev" \
     -F "file=@test.pdf" \
     -F "session_id=test123"
   ```

2. **查询上传的记录**
   ```bash
   curl -X GET "http://localhost:5000/queue_state?username=dev&status=uploaded"
   ```

3. **提交到队列**
   ```bash
   curl -X POST http://localhost:5000/submit_queued_tasks \
     -H "Content-Type: application/json" \
     -d '{
       "username": "dev",
       "attachment_names": ["test.pdf"]
     }'
   ```

4. **查看队列状态**
   ```bash
   curl -X GET "http://localhost:5000/queue_state?username=dev&status=queued"
   ```

## 注意事项

1. **错误处理**
   - 上传失败时需要清理已上传的文件
   - 提交失败时需要回滚数据库状态
   - 处理失败时需要释放锁

2. **性能优化**
   - 批量提交时考虑分页处理
   - 轮询间隔不宜过短（建议 3-5 秒）
   - 考虑使用 WebSocket 替代轮询

3. **安全性**
   - 验证用户权限
   - 限制单个用户的并发数
   - 设置合理的超时时间

## 相关文件

- 后端实现：
  - `app/system/homepage.py` - 文件上传
  - `app/system/task_queue.py` - 队列管理和并发控制
  
- 前端组件：
  - `vue-project_ver2/src/components/ChatArea.vue` - 聊天界面
  
- 测试脚本：
  - `test_submit_flow.py` - 提交流程测试
  - `verify_queue_concurrency.py` - 并发控制测试
