#!/usr/bin/env bash
# 用法：
#   ./scripts/build-push.sh                    # 只构建
#   REGISTRY=registry.cn-hangzhou.aliyuncs.com NAMESPACE=your-ns ./scripts/build-push.sh push
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-sentiment-agents}"
VERSION="${VERSION:-1.0.0}"
REGISTRY="${REGISTRY:-registry.cn-hangzhou.aliyuncs.com}"
NAMESPACE="${NAMESPACE:-}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PUSH="${1:-}"

LOCAL_TAG="${IMAGE_NAME}:${VERSION}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

echo "==> 构建镜像 ${LOCAL_TAG}"
docker build --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" -t "${LOCAL_TAG}" .

if [[ "$PUSH" == "push" ]]; then
  if [[ -z "$NAMESPACE" ]]; then
    echo "推送需要设置 NAMESPACE，例如 NAMESPACE=your-ns $0 push" >&2
    exit 1
  fi
  REMOTE_TAG="${REGISTRY}/${NAMESPACE}/${LOCAL_TAG}"
  echo "==> 打标签 ${REMOTE_TAG}"
  docker tag "${LOCAL_TAG}" "${REMOTE_TAG}"
  echo "==> 推送（未登录请先执行 docker login --username=<账号> ${REGISTRY}）"
  docker push "${REMOTE_TAG}"
  echo "==> 完成：${REMOTE_TAG}"
else
  echo "==> 本地构建完成：${LOCAL_TAG}"
fi
