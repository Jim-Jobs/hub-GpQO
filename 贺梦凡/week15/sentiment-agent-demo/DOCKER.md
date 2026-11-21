# OpenAI Agents 版 · Docker 与阿里云镜像打包流程

基于作业 1 的 **openai-agents** 实现（`openai_agents_impl/` + `shared/` + `frontend/`），使用本目录 `Dockerfile` 构建镜像，并推送到 **阿里云容器镜像服务 ACR**。

---

## 1. 产物说明

| 文件 | 作用 |
|------|------|
| `Dockerfile` | Agents 版镜像构建文件 |
| `requirements-agents.txt` | 镜像内依赖（不含 LangGraph） |
| `.dockerignore` | 排除 `.venv`、`.env`、`langgraph_impl` 等 |

镜像内启动：`uvicorn openai_agents_impl.server:app --host 0.0.0.0 --port 8002`  
对外提供：页面 `/`、接口 `POST /api/chat`、健康检查 `GET /health`。

**密钥不要打进镜像**，运行时用 `-e` 注入 `OPENAI_API_KEY` 等。

---

## 2. 本地构建与试跑

在 `sentiment-agent-demo/` 目录执行：

```bash
# 构建
docker build -t sentiment-agents:v1 .

# 本地运行（替换为你的有效 Key）
docker run --rm -p 8002:8002 \
  -e OPENAI_API_KEY=sk-your-key \
  -e OPENAI_BASE_URL=https://api.deepseek.com \
  -e OPENAI_MODEL=deepseek-chat \
  sentiment-agents:v1
```

浏览器访问：http://127.0.0.1:8002  

```bash
curl http://127.0.0.1:8002/health
curl -X POST http://127.0.0.1:8002/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"今天面试通过了，好开心！\"}"
```

---

## 3. 阿里云 ACR 准备

1. 登录 [容器镜像服务 ACR](https://cr.console.aliyun.com/)  
2. 开通「个人版」或「企业版」实例  
3. 创建 **命名空间**（例：`ai-lesson`）  
4. 创建 **镜像仓库**（例：`sentiment-agents`，可选「本地仓库」）  
5. 在仓库页查看：
   - **公网地址**，形如：`registry.cn-hangzhou.aliyuncs.com`
   - **登录用户名**：一般为阿里云账号全名（邮箱/手机号对应账号）
   - **登录密码**：在「访问凭证」里设置（不是阿里云登录密码）

记下完整镜像路径模板：

```text
registry.<地域>.aliyuncs.com/<命名空间>/<仓库名>:<标签>
# 例：
registry.cn-hangzhou.aliyuncs.com/ai-lesson/sentiment-agents:v1
```

> 地域按你控制台实际为准：`cn-hangzhou` / `cn-shanghai` / `cn-beijing` 等。

---

## 4. 打包并推送到阿里云（推荐流程）

以下命令在 **`sentiment-agent-demo/`** 下执行。请把占位符换成你的真实值。

### 步骤 A：登录 ACR

```bash
docker login --username=<阿里云账号名> registry.cn-hangzhou.aliyuncs.com
# 按提示输入「访问凭证」密码
```

Windows PowerShell 同样可用上述命令。

### 步骤 B：构建镜像

```bash
docker build -t sentiment-agents:v1 .
```

可选：指定平台（云服务器多为 linux/amd64）：

```bash
docker build --platform linux/amd64 -t sentiment-agents:v1 .
```

### 步骤 C：打阿里云标签

```bash
docker tag sentiment-agents:v1 \
  registry.cn-hangzhou.aliyuncs.com/ai-lesson/sentiment-agents:v1
```

### 步骤 D：推送

```bash
docker push registry.cn-hangzhou.aliyuncs.com/ai-lesson/sentiment-agents:v1
```

推送成功后，在 ACR 控制台仓库的「镜像版本」中应能看到 `v1`。

### 步骤 E：从阿里云拉取并运行

```bash
docker pull registry.cn-hangzhou.aliyuncs.com/ai-lesson/sentiment-agents:v1

docker run -d --name sentiment-agents -p 8002:8002 \
  -e OPENAI_API_KEY=sk-your-key \
  -e OPENAI_BASE_URL=https://api.deepseek.com \
  -e OPENAI_MODEL=deepseek-chat \
  registry.cn-hangzhou.aliyuncs.com/ai-lesson/sentiment-agents:v1
```

---

## 5. 一键脚本（可选）

将下面保存为 `build-push-acr.ps1`（PowerShell）后按需改变量：

```powershell
$Registry = "registry.cn-hangzhou.aliyuncs.com"
$Namespace = "ai-lesson"
$Repo = "sentiment-agents"
$Tag = "v1"
$Image = "${Registry}/${Namespace}/${Repo}:${Tag}"

docker build --platform linux/amd64 -t "${Repo}:${Tag}" .
docker tag "${Repo}:${Tag}" $Image
docker login --username=$env:ACR_USERNAME $Registry
docker push $Image
Write-Host "Pushed: $Image"
```

```powershell
$env:ACR_USERNAME = "你的阿里云账号"
.\build-push-acr.ps1
```

---

## 6. 常见问题

| 现象 | 处理 |
|------|------|
| `docker login` 失败 | 用 ACR「访问凭证」密码；确认地域域名与仓库一致 |
| `denied` / 推送无权限 | 检查命名空间、仓库名是否存在；个人版是否欠费/未开通 |
| Apple Silicon 上云跑不起来 | 构建加 `--platform linux/amd64` |
| 容器内调 LLM 401 | `-e OPENAI_API_KEY` 是否有效；未把 `.env` 打进镜像属预期 |
| 本机访问不到页面 | 确认 `-p 8002:8002`，且 CMD 使用 `0.0.0.0` |

---

## 7. 流程小结

```text
写好 Dockerfile
    → docker build（本地镜像 sentiment-agents:v1）
    → ACR 建命名空间 + 仓库
    → docker login 阿里云镜像仓库
    → docker tag 成 registry.cn-xxx.aliyuncs.com/命名空间/仓库:标签
    → docker push
    → 任意机器 docker pull + docker run -e OPENAI_API_KEY=...
```
