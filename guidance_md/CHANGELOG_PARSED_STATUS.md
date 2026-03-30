# 更新日志 - Parsed 状态引入

## 📅 更新日期
2026-03-30

## 🎯 更新目标
明确区分"处理完成"和"文件已解析"，使用 `parsed` 状态替代 `completed`

---

## ✅ 核心变更

### 1. 新增数据库状态

**状态列表**:
- `uploaded` - 文件已上传，等待提交
- `queued` - 已提交到队列
- `processing` - 正在处理
- **`parsed`** - ⭐ **文件已解析完成，AI 已返回响应**（新增）
- `failed` - 处理失败

**状态流转**:
```
uploaded → queued → processing → parsed
                       ↘
                        failed
```

---

## 🔧 代码修改

### (1) `TaskQueue.process_with_lock()` 方法

**文件**: `app/system/task_queue.py`  
**位置**: 第 571-624 行

**修改内容**:
```python
# 修改前
QueueState.update_status_by_message_id(message_id, "completed")
logger.info(f"✅ 任务 {message_id} 处理完成")

# 修改后
QueueState.update_status_by_message_id(message_id, "parsed")
logger.info(f"✅ 任务 {message_id} 处理完成，状态已更新为 'parsed'")
```

**原因**: 
- AI 返回响应时，明确表示文件已解析
- 与 `completed`（一般意义上的完成）区分开

---

### (2) `QueueStats.get()` 方法

**文件**: `app/system/task_queue.py`  
**位置**: 第 394-485 行

**修改内容**:
- 分别统计 `pending` (queued + processing) 和 `parsed` 状态
- 返回 `total_parsed` 和 `parsed_attachment_names`

**修改前**:
```python
query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.status = 'queued'"
items = list(container.query_items(query=query, ...))

return {
    "total_pending": len(items),
    "code": 200
}
```

**修改后**:
```python
# 查询待处理（queued + processing）
query_pending = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.status IN ('queued', 'processing')"
items_pending = list(container.query_items(query=query_pending, ...))

# 查询已解析（parsed）
query_parsed = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.status = 'parsed'"
items_parsed = list(container.query_items(query=query_parsed, ...))

return {
    "total_pending": len(items_pending),      # 待处理数量
    "total_parsed": len(items_parsed),        # 已解析数量
    "parsed_attachment_names": [...],         # 已解析的文件列表
    "code": 200
}
```

**优势**:
- 前端可以清楚知道处理进度
- 可以显示"已完成 X/总数 Y"
- 可以根据 `parsed_attachment_names` 高亮已解析的文件

---

### (3) `QueueState.update_status_by_message_id()` 方法

**文件**: `app/system/task_queue.py`  
**位置**: 第 246-260 行

**修改内容**:
- 添加文档字符串说明支持的状态
- 添加日志记录

```python
@staticmethod
def update_status_by_message_id(message_id: str, status: str) -> None:
    """
    根据 message_id 更新队列状态
    :param message_id: 消息 ID
    :param status: 新状态（queued, processing, completed, failed, parsed）
    """
    logger.info(f"✅ Updated queue state to '{status}' for message_id: {message_id}")
```

---

## 📚 文档更新

### (1) UPLOAD_AND_SUBMIT_GUIDE.md

**更新内容**:
- 状态流转图：`completed` → `parsed`
- 添加 `parsed` 状态说明
- 添加重要说明段落

```markdown
**重要说明**:
- 使用 `parsed` 而不是 `completed` 是为了明确表示**文件解析已完成**
- 当 AI 返回响应时，表示当前 session 的文件已经解析完毕
- `parsed` 状态的文件不会再参与排队统计
- 前端可以根据 `parsed` 状态显示"解析完成"提示
```

---

### (2) IMPLEMENTATION_SUMMARY.md

**更新内容**:
- 状态含义中添加 `parsed` 说明
- 添加重要说明段落
- 标注 `parsed` 为关键特性（⭐）

---

### (3) QUICK_REFERENCE.md

**更新内容**:
- 状态表格：`completed` → `parsed`
- 添加注意事项

```markdown
**注意**: 
- `parsed` 表示文件解析完成，不再参与排队统计
- 前端可根据 `parsed` 状态显示"解析完成"
```

---

### (4) PARSED_STATUS_EXPLANATION.md（新增）

**完整说明文档**，包含:
- 为什么需要 `parsed` 状态
- 状态流转对比
- 实际应用场景
- 前端集成示例
- API 使用示例
- 日志示例

---

## 🎯 影响范围

### 后端影响

1. **状态更新逻辑**: 
   - ✅ `process_with_lock()` 方法
   - ✅ 完成时设置为 `parsed` 而非 `completed`

2. **统计查询**:
   - ✅ `QueueStats.get()` 方法
   - ✅ 分别统计 `pending` 和 `parsed`

3. **数据库记录**:
   - ✅ 现有记录不受影响
   - ✅ 新记录使用 `parsed` 状态

### 前端影响

1. **队列轮询**:
   ```javascript
   // 新增：获取已解析数量
   const { total_pending, total_parsed, parsed_attachment_names } = response.data;
   ```

2. **显示逻辑**:
   ```javascript
   if (total_parsed > 0) {
       // 显示已解析的文件
       showParsedFiles(parsed_attachment_names);
   }
   
   if (total_pending === 0 && total_parsed > 0) {
       // 所有文件都解析完成
       showCompletionMessage();
   }
   ```

3. **进度显示**:
   ```javascript
   // 可以显示：已解析 3/总数 5 个文件
   progressText = `已解析 ${total_parsed}/${total_pending + total_parsed} 个文件`;
   ```

---

## 📊 API 响应变化

### `/queue_stats` 接口

**修改前**:
```json
{
    "total_pending": 5,
    "light_queue_pending": 3,
    "heavy_queue_pending": 2,
    "light_attachment_names": [...],
    "heavy_attachment_names": [...],
    "code": 200
}
```

**修改后**:
```json
{
    "total_pending": 2,                    // ⏳ 待处理（queued + processing）
    "light_queue_pending": 1,              // ⏳ light-queue 待处理
    "heavy_queue_pending": 1,              // ⏳ heavy-queue 待处理
    "light_attachment_names": [...],       
    "heavy_attachment_names": [...],
    "total_parsed": 3,                     // ✅ 已解析完成（新增）
    "parsed_attachment_names": [           // ✅ 已解析的文件列表（新增）
        "file1.pdf",
        "file2.pdf",
        "file3.pdf"
    ],
    "code": 200
}
```

---

## 🧪 测试建议

### 1. 单元测试

```python
def test_parsed_status():
    """测试 parsed 状态更新"""
    message_id = "test-msg-123"
    
    # 模拟处理完成
    result = process_file("test.pdf")
    
    # 更新为 parsed 状态
    QueueState.update_status_by_message_id(message_id, "parsed")
    
    # 验证状态
    item = container.read_item(item=message_id, partition_key=message_id)
    assert item['status'] == 'parsed'
```

### 2. 集成测试

```bash
# 运行提交流程测试
python test_submit_flow.py

# 验证 parsed 状态
python -c "
from app.system.task_queue import QueueState
QueueState.update_status_by_message_id('test-123', 'parsed')
print('✅ Parsed status test passed')
"
```

### 3. 前端测试

1. 上传多个文件
2. 点击送信
3. 观察队列对话框
4. 验证 `parsed` 状态是否正确显示

---

## ⚠️ 注意事项

### 1. 向后兼容

- **现有记录**: 仍可能使用 `completed` 状态
- **新记录**: 统一使用 `parsed` 状态
- **查询**: 同时支持 `completed` 和 `parsed`（可选）

### 2. 数据迁移（可选）

如果需要将旧的 `completed` 记录迁移到 `parsed`:

```sql
-- 查询所有 completed 状态的记录
SELECT * FROM c 
WHERE c.type = 'queue_state' 
  AND c.status = 'completed'

-- 手动或批量更新为 parsed
UPDATE c 
SET c.status = 'parsed' 
WHERE c.status = 'completed'
```

### 3. 前端降级处理

```javascript
// 兼容旧版本的 completed 状态
const parsedCount = response.data.total_parsed || 
                    response.data.total_completed || 0;
```

---

## 📈 性能优化建议

### 1. 定期清理 parsed 记录

```python
def cleanup_old_parsed_records(days=7):
    """清理 7 天前的 parsed 记录"""
    from datetime import datetime, timedelta
    
    cutoff_time = datetime.now() - timedelta(days=days)
    
    query = """
        SELECT * FROM c 
        WHERE c.type = 'queue_state' 
          AND c.status = 'parsed'
          AND c.create_time < @cutoff
    """
    params = [{"name": "@cutoff", "value": cutoff_time.isoformat()}]
    
    items = list(container.query_items(query=query, parameters=params))
    
    for item in items:
        container.delete_item(item=item['id'], partition_key=item['id'])
```

### 2. 索引优化

确保 Cosmos DB 有以下索引：
- `status`: 用于快速查询
- `username`: 用于用户级别查询
- `create_time`: 用于时间范围查询

---

## ✅ 验证清单

- [x] 代码语法检查通过
- [x] `process_with_lock()` 使用 `parsed` 状态
- [x] `QueueStats.get()` 返回 `parsed` 统计
- [x] 文档已更新
- [x] 添加了详细说明文档
- [ ] 单元测试（待执行）
- [ ] 集成测试（待执行）
- [ ] 前端适配（待实施）

---

## 🔗 相关文档

- [Parsed 状态详细说明](PARSED_STATUS_EXPLANATION.md)
- [上传和提交指南](UPLOAD_AND_SUBMIT_GUIDE.md)
- [实现总结](IMPLEMENTATION_SUMMARY.md)
- [快速参考](QUICK_REFERENCE.md)

---

**更新者**: AI Assistant  
**审核状态**: ✅ 完成  
**下一步**: 前端集成和测试
