# 队列并发控制机制使用说明

## 概述

基于 Cosmos DB 的全局锁机制，实现了严格的队列并发控制：
- **heavy-queue**: 最多允许 **1 个** 任务同时处理
- **light-queue**: 最多允许 **2 个** 任务同时处理
- 超过并发限制的任务会自动排队等待

## 核心组件

### 1. QueueConcurrencyLock 类

位于 `app/system/task_queue.py`，提供以下功能：

```python
class QueueConcurrencyLock:
    """队列并发锁控制类"""
    
    # 配置常量
    MAX_HEAVY_TASKS = 1          # heavy-queue 最大并发数
    MAX_LIGHT_TASKS = 2          # light-queue 最大并发数
    RETRY_INTERVAL_SECONDS = 3   # 重试间隔（秒）
    PROCESSING_TIMEOUT_MINUTES = 10  # 超时时间（分钟）
```

#### 主要方法：

- **`acquire_lock(queue_name, message_id)`**: 获取处理锁
  - 如果当前有可用槽位，立即获取锁
  - 如果没有可用槽位，阻塞等待直到有空闲
  - 自动检测和释放超时锁

- **`release_lock(queue_name, message_id)`**: 释放处理锁
  - 从活跃槽位中移除指定消息
  - 更新 Cosmos DB 中的锁状态

### 2. TaskQueue.process_with_lock() 静态方法

带锁的任务处理方法，封装了完整的锁管理流程：

```python
@staticmethod
def process_with_lock(
    queue_name: str,           # 队列名称："heavy-queue" 或 "light-queue"
    message_id: str,           # 消息 ID
    processor_func,            # 实际的处理函数
    *args,                     # 处理函数的参数
    **kwargs                   # 处理函数的关键字参数
)
```

#### 执行流程：

1. **获取锁** - 调用 `QueueConcurrencyLock.acquire_lock()`
2. **更新状态** - 将队列状态更新为 "processing"
3. **执行处理** - 调用传入的 `processor_func`
4. **完成状态** - 将队列状态更新为 "completed"
5. **释放锁** - 在 finally 块中确保锁被释放

## 使用方法

### 方式一：使用辅助函数 `call_with_queue_lock()`

这是最简单的使用方式：

```python
from app.system.task_queue import call_with_queue_lock

@app.route('/api/process_task', methods=['POST'])
def process_task():
    data = request.get_json()
    username = data.get('username')
    queue_name = data.get('queue_name')
    message_id = data.get('message_id')
    attachment_names = data.get('attachment_names')
    
    try:
        # 定义你的业务处理函数
        def my_processor(username, attachment_names, message_data):
            # 这里写你的实际业务逻辑
            # 例如：调用 OpenAI API、处理文件等
            result = your_openai_call(message_data)
            return result
        
        # 调用带锁的任务处理
        result = call_with_queue_lock(
            username=username,
            queue_name=queue_name,
            message_id=message_id,
            attachment_names=attachment_names,
            message_data=data,
            processor_func=my_processor
        )
        
        return jsonify({
            "success": True,
            "result": result
        }), 200
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
```

### 方式二：直接使用 `TaskQueue.process_with_lock()`

如果需要更多控制权：

```python
from app.system.task_queue import TaskQueue, QueueState

def your_business_logic(message_data, username, attachments):
    """你的业务处理函数"""
    # 1. 处理文件
    for attachment in attachments:
        process_file(attachment)
    
    # 2. 调用 AI API
    response = call_your_ai_api(message_data)
    
    # 3. 保存结果
    save_result(response)
    
    return response

# 在你的 API 端点中
message_id = "your-message-id-123"
queue_name = "heavy-queue"  # 或 "light-queue"

try:
    result = TaskQueue.process_with_lock(
        queue_name=queue_name,
        message_id=message_id,
        processor_func=your_business_logic,
        message_data=data,
        username=username,
        attachment_names=attachments
    )
    # 处理成功
except Exception as e:
    # 处理失败（锁会自动释放）
    logging.error(f"Task failed: {e}")
```

## 完整示例

### 示例 1: 文件处理 API

```python
from flask import Flask, request, jsonify
from app.system.task_queue import call_with_queue_lock
import uuid

app = Flask(__name__)

@app.route('/api/upload_and_process', methods=['POST'])
def upload_and_process():
    """上传文件并异步处理"""
    data = request.get_json()
    username = data.get('username')
    files = data.get('files', [])
    
    # 1. 创建消息
    message_id = str(uuid.uuid4())
    queue_name = "heavy-queue" if len(files) > 5 else "light-queue"
    
    # 2. 定义处理函数
    def file_processor(username, attachment_names, message_data):
        results = []
        for file in attachment_names:
            # 读取文件
            content = read_file(file)
            # 调用 AI 分析
            analysis = call_ai_api(content)
            results.append(analysis)
        return {"results": results}
    
    # 3. 提交到队列（带锁处理）
    try:
        result = call_with_queue_lock(
            username=username,
            queue_name=queue_name,
            message_id=message_id,
            attachment_names=files,
            message_data=data,
            processor_func=file_processor
        )
        
        return jsonify({
            "status": "completed",
            "result": result
        })
        
    except Exception as e:
        return jsonify({
            "status": "failed",
            "error": str(e)
        }), 500
```

### 示例 2: OpenAI 调用 API

```python
@app.route('/api/call_openai_queued', methods=['POST'])
def call_openai_queued():
    """通过队列机制调用 OpenAI"""
    data = request.get_json()
    messages = data.get('messages', [])
    username = data.get('username')
    
    # 计算 token 数以决定队列
    token_count = estimate_tokens(messages)
    queue_name = "heavy-queue" if token_count > 50000 else "light-queue"
    message_id = str(uuid.uuid4())
    
    def openai_processor(username, attachment_names, message_data):
        # 这里调用你的 OpenAI 逻辑
        from app.system.homepage import openai_with_global_lock_gpt5
        
        response = openai_with_global_lock_gpt5(
            messages=message_data['messages'],
            image_bytes=message_data.get('image_bytes')
        )
        return response.to_dict()
    
    try:
        result = call_with_queue_lock(
            username=username,
            queue_name=queue_name,
            message_id=message_id,
            attachment_names=[],
            message_data=data,
            processor_func=openai_processor
        )
        
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

## 状态流转

任务状态变化：

```
queued → processing → completed
                  ↘
                   failed
```

- **queued**: 任务已提交到队列，等待处理
- **processing**: 已获得锁，正在处理
- **completed**: 处理完成
- **failed**: 处理失败

## 高级特性

### 1. 超时保护

- 每个锁槽位有 **10 分钟** 的超时时间
- 超时的锁会被自动检测和释放
- 防止死锁和任务卡住

### 2. 自动重试

- 获取锁失败时自动重试
- 重试间隔：**3 秒 + 随机延迟**
- 避免并发冲突

### 3. 分布式友好

- 锁状态存储在 Cosmos DB
- 支持多实例部署
- 全局唯一的锁视图

## 监控和调试

### 日志输出

系统会输出详细的日志：

```
🔵 开始为任务 abc-123 获取 heavy-queue 锁...
✅ 任务 abc-123 成功获取锁，开始处理...
⚙️ 正在处理任务...
✅ 任务 abc-123 处理完成
✅ 成功释放 heavy-queue 锁槽位，当前活跃数：0
```

### 查看队列状态

使用 `/queue_state` 接口查看当前队列状态：

```bash
GET /api/queue_state?status=processing
```

返回所有正在处理的任务：

```json
{
  "queue_state": [
    {
      "message_id": "abc-123",
      "queue_name": "heavy-queue",
      "status": "processing",
      "username": "user1"
    }
  ]
}
```

## 注意事项

1. **处理器函数应该是幂等的**
   - 因为可能会因为超时而重试
   
2. **避免长时间持有锁**
   - 尽量优化处理逻辑
   - 如果预计处理时间 > 10 分钟，需要考虑分段处理

3. **错误处理**
   - 所有异常都会触发锁释放
   - 建议在处理器中捕获并记录错误

4. **性能考虑**
   - heavy-queue 是串行处理
   - light-queue 是 2 个并行处理
   - 根据任务特点选择合适的队列

## 测试

运行测试脚本验证并发控制：

```bash
python test_queue_concurrency.py
```

该测试会：
1. 同时提交 3 个 heavy 任务（验证只有 1 个在执行）
2. 同时提交 5 个 light 任务（验证最多 2 个同时执行）

## 总结

通过这个并发控制机制，你可以：
- ✅ 严格控制同一时间的任务处理数量
- ✅ 防止系统过载
- ✅ 确保关键资源不被过度占用
- ✅ 支持分布式部署
- ✅ 自动故障恢复

这个机制特别适合需要严格限制并发数的场景，如：
- 调用受限的 API
- 访问共享资源
- CPU/内存密集型任务
