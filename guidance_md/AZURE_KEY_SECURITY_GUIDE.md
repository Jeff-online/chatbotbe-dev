# Azure Key 安全配置指南

## ⚠️ 重要警告

**绝对不要将 Azure Key、Connection String 等敏感信息提交到 GitHub！**

这会导致严重的安全风险：
- 🔴 未授权访问 Azure 资源
- 🔴 数据泄露
- 🔴 产生意外费用
- 🔴 违反合规要求

---

## ✅ 已删除的敏感文件

以下目录已被删除，因为包含硬编码的 Azure Key：

```
❌ test_scripts/test_queue_flow_v3.py
   - 包含：STORAGE_CONNECTION_STRING = "DefaultEndpointsProtocol=...AccountKey=/JQJc7xQbeKfKuvz+..."
```

---

## 🛡️ 正确的配置方式

### 1. 使用环境变量（推荐）

**创建 `.env` 文件**（已在 `.gitignore` 中）：

```bash
# .env 文件（不要提交到 Git！）
AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=yourname;AccountKey=yourkey;EndpointSuffix=core.windows.net"
APPLICATIONINSIGHTS_CONNECTION_STRING="InstrumentationKey=your-key"
COSMOS_URI="https://your-cosmos-account.documents.azure.com:443/"
```

**在代码中使用**：

```python
import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 从环境变量获取
connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
cosmos_uri = os.getenv("COSMOS_URI")
```

---

### 2. 使用 Azure Identity（生产环境推荐）

**安装依赖**：

```bash
pip install azure-identity
```

**代码示例**：

```python
from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient
from azure.storage.blob import BlobServiceClient

# 使用托管身份或 CLI 登录
credential = DefaultAzureCredential()

# Cosmos DB
client = CosmosClient(cosmos_uri, credential=credential)

# Blob Storage（用于存储用户上传的文件）
blob_service_client = BlobServiceClient(account_url, credential=credential)
```

---

### 3. 本地开发使用 Azure CLI

**步骤**：

1. **安装 Azure CLI**
   ```bash
   # Windows
   winget install Microsoft.AzureCLI
   
   # macOS
   brew install azure-cli
   ```

2. **登录**
   ```bash
   az login
   ```

3. **设置订阅**
   ```bash
   az account set --subscription "your-subscription-id"
   ```

4. **代码自动使用 CLI 凭据**
   ```python
   from azure.identity import DefaultAzureCredential
   
   credential = DefaultAzureCredential()
   # 会自动使用 az login 的凭据
   ```

---

## 📝 测试脚本的正确写法

### ❌ 错误示例（硬编码 Key）

```python
# 绝对不要这样做！
STORAGE_CONNECTION_STRING = "DefaultEndpointsProtocol=...AccountKey=xxx"
```

### ✅ 正确示例（使用环境变量）

```python
import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 从环境变量获取
STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")

if not STORAGE_CONNECTION_STRING:
    raise ValueError("AZURE_STORAGE_CONNECTION_STRING must be set")
```

---

## 🔒 如果 Key 已经泄露

### 立即行动

1. **撤销泄露的 Key**
   ```bash
   # Azure Portal → Storage Account → Security + networking → Access keys
   # 点击 "Rotate key" 重新生成
   ```

2. **更新所有使用该 Key 的地方**
   - 本地 `.env` 文件
   - 服务器环境变量
   - CI/CD 配置

3. **检查访问日志**
   ```bash
   # Azure Monitor → Logs
   # 查询异常的访问记录
   ```

4. **提交新的 Key（通过安全方式）**
   - 使用 Azure Key Vault
   - 使用 GitHub Secrets
   - 使用环境变量

---

## 📂 项目中的安全配置

### 当前项目的配置

**后端代码**（✅ 正确）：

```python
# app/system/task_queue.py
connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")

if not connection_string:
    logger.warning("⚠️ AZURE_STORAGE_CONNECTION_STRING not set")
    raise messages.InterfaceCallError("AZURE_STORAGE_CONNECTION_STRING not configured")
```

**配置文件**（✅ 正确）：

```python
# config.py
APP_INSIGHTS_CONNECTION_STRING = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
```

**测试脚本**（❌ 已删除）：

```python
# test_scripts/test_queue_flow_v3.py - 已删除
STORAGE_CONNECTION_STRING = "DefaultEndpointsProtocol=...AccountKey=..."  # ❌ 硬编码
```

---

## 🎯 最佳实践总结

### 1. 永远不要硬编码

```python
# ❌ 错误
KEY = "sk-abc123..."
CONNECTION_STRING = "DefaultEndpointsProtocol=...AccountKey=xxx"

# ✅ 正确
KEY = os.getenv("API_KEY")
CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
```

### 2. 使用 .gitignore

确保 `.env` 文件不会被提交：

```gitignore
# .gitignore
.env
.env.local
.env.*.local
*.env
```

### 3. 使用密钥管理服务

- **Azure Key Vault**（推荐）
- AWS Secrets Manager
- HashiCorp Vault
- GitHub Secrets（用于 CI/CD）

### 4. 定期轮换密钥

```bash
# 设置提醒，每 90 天轮换一次
# Azure Portal → Policy → Compliance
```

### 5. 最小权限原则

```bash
# 不要使用 Storage Account 的完全访问密钥
# 而是使用 SAS Token 或 Managed Identity

# 例如：只允许访问特定队列
az storage queue generate-sas \
  --account-name myaccount \
  --name myqueue \
  --permissions r \
  --expiry 2024-12-31
```

---

## 🚨 检测泄露的工具

### 1. Git 历史扫描

```bash
# 安装 truffleHog
pip install truffleHog

# 扫描 Git 历史
trufflehog git file://. --since-commit HEAD --branch main --fail
```

### 2. GitHub Secret Scanning

- 启用 GitHub Advanced Security
- 自动检测提交的密钥
- 接收安全警报

### 3. pre-commit hooks

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.4.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']
```

---

## 📞 紧急联系

如果发现密钥泄露：

1. **立即撤销**：Azure Portal → Storage Account → Access keys
2. **通知团队**：发送安全警报
3. **审计日志**：检查是否有未授权访问
4. **更新配置**：使用新密钥替换

---

**创建日期**: 2026-03-30  
**安全等级**: 🔴 高优先级
