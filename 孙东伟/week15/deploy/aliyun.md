# 作业 2：推送到阿里云容器镜像服务（ACR）

本文件记录把 `sentiment-companion:agents` 镜像推到阿里云并在服务器上运行的完整步骤。
命令以 PowerShell 为例，Linux/macOS 去掉 `.\` 前缀即可。

---

## 1. 创建镜像仓库

1. 登录 [容器镜像服务控制台](https://cr.console.aliyun.com/)。
2. 选择地域（如 `cn-hangzhou`），进入「个人实例」。
3. 首次使用先设置 **Registry 登录密码**（固定密码，不是阿里云账号密码）。
4. 创建命名空间，例如 `week15`。
5. 在命名空间下创建仓库 `sentiment-companion`，类型选「私有」。

仓库完整地址形如：

```
registry.cn-hangzhou.aliyuncs.com/week15/sentiment-companion
```

> 如果用的是企业版，地址形如 `xxx-registry.cn-hangzhou.cr.aliyuncs.com/week15/sentiment-companion`，
> 下面的命令把 `-Registry` 换成对应地址即可。

---

## 2. 构建并推送

```powershell
# 登录（用户名是阿里云账号，密码是第 1 步设置的 Registry 密码）
docker login --username=你的阿里云账号 registry.cn-hangzhou.aliyuncs.com

# 一键构建 + 打标签 + 推送
.\scripts\push-acr.ps1 -Namespace week15 -Repo sentiment-companion -Tag v1
```

等价的纯 Docker 命令：

```powershell
docker build -t sentiment-companion:agents .
docker tag sentiment-companion:agents registry.cn-hangzhou.aliyuncs.com/week15/sentiment-companion:v1
docker push registry.cn-hangzhou.aliyuncs.com/week15/sentiment-companion:v1
```

国内网络拉不到 `python:3.12-slim` 时，先换基础镜像源再构建：

```powershell
docker build `
  --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim `
  -t sentiment-companion:agents .
```

构建完成后 `docker images` 应能看到镜像，大小约 300–450 MB（取决于依赖版本）。

---

## 3. 在 ECS 上拉取运行

```bash
docker login --username=你的阿里云账号 registry.cn-hangzhou.aliyuncs.com
docker pull registry.cn-hangzhou.aliyuncs.com/week15/sentiment-companion:v1
docker run -d --name sentiment-api -p 8000:8000 \
  -e APP_MODE=live \
  -e LLM_API_KEY=sk-xxx \
  -e LLM_BASE_URL=https://api.openai.com/v1 \
  --restart unless-stopped \
  registry.cn-hangzhou.aliyuncs.com/week15/sentiment-companion:v1
```

验证：

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"ok","mode":"live","model":"gpt-4o-mini","default_engine":"agents","engines":["agents","langgraph"]}
```

---

## 4. 用 ACR 的「构建规则」在线构建（可选）

如果不想在本机构建（或本机带宽太差），可以在 ACR 控制台给仓库配置构建规则：

1. 仓库 →「构建」→「构建规则」→ 添加规则。
2. 类型选「Branch/Tag」，绑定代码仓库（GitHub / Gitee / 云效 Codeup）。
3. Dockerfile 路径填 `Dockerfile`，构建上下文填 `/`。
4. 推送 Tag 触发构建，产物自动进入当前仓库。

这样作业提交时既有了 Dockerfile，也有了云端构建记录，答辩更好讲。

---

## 5. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `dial tcp ... i/o timeout` 拉基础镜像 | Docker Hub 不可达 | 用 `--build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim` |
| `unauthorized: authentication required` | 未登录或密码错 | `docker login`，个人版用 Registry 固定密码 |
| 推送很慢 | 上传带宽限制 | 镜像层已缓存，重推只会传变化的层；可先 `docker save` 再 `scp` 到 ECS `docker load` |
| 容器启动即退出 | 端口占用 / 环境变量错误 | `docker logs sentiment-api` |
| 拉取后 `pip install` 失败 | 构建机访问不到阿里云 PyPI | 构建时覆盖 `--build-arg PIP_INDEX_URL=https://pypi.org/simple/` |
