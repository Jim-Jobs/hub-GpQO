# 1. 指定基础镜像：选择轻量级的 Python 3.11 官方镜像
FROM python:3.11-slim

# 2. 设置容器内的工作目录
WORKDIR /app

# 3. 先复制依赖清单（利用 Docker 的缓存机制）
COPY requirements.txt .

# 4. 安装依赖（这里顺手替换成了清华源，防止国内构建时下载太慢或断连）
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 5. 将当前目录下的所有业务代码复制到容器的 /app 目录下
COPY . .

# 6. 声明服务监听的端口（对应我们之前的 8001 端口）
EXPOSE 8001

# 7. 启动命令：必须是 0.0.0.0，否则宿主机无法访问容器的服务
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]