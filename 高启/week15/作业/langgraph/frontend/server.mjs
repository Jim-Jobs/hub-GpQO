/** 提供聊天页面，并按环境变量生成浏览器使用的 API 地址配置。 */

import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(fileURLToPath(new URL(".", import.meta.url)));
const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
};

/** 以 JSON 编码生成前端可用的 API 基地址脚本。 */
function createClientConfig() {
  /** @type {string} */
  const host = process.env.API_HOST || "localhost";
  /** @type {string} */
  const port = process.env.API_PORT || "8000";
  return `window.APP_CONFIG = { apiBaseUrl: ${JSON.stringify(`http://${host}:${port}`)} };\n`;
}

/** 将请求路径限制在前端目录内，避免读取目录之外的文件。
 * @param {string} pathname URL 解码后的请求路径。
 * @returns {string|null} 安全的文件绝对路径；越界时返回 null。
 */
function resolveAssetPath(pathname) {
  const requestedPath = pathname === "/" ? "/index.html" : pathname;
  const filePath = resolve(ROOT, `.${requestedPath}`);
  return filePath === ROOT || filePath.startsWith(`${ROOT}${sep}`) ? filePath : null;
}

/** 处理配置脚本和前端静态文件请求。
 * @param {import('node:http').IncomingMessage} request 浏览器 HTTP 请求。
 * @param {import('node:http').ServerResponse} response HTTP 响应对象。
 * @returns {Promise<void>} 完成响应后结束。
 */
async function handleRequest(request, response) {
  const pathname = new URL(request.url || "/", "http://localhost").pathname;
  if (pathname === "/config.js") {
    response.writeHead(200, { "Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store" });
    response.end(createClientConfig());
    return;
  }

  let decodedPath;
  try {
    decodedPath = decodeURIComponent(pathname);
  } catch {
    response.writeHead(400);
    response.end("Bad request");
    return;
  }
  const filePath = resolveAssetPath(decodedPath);
  if (!filePath) {
    response.writeHead(404);
    response.end("Not found");
    return;
  }

  try {
    const fileInfo = await stat(filePath);
    if (!fileInfo.isFile()) throw new Error("不是普通文件");
    response.writeHead(200, {
      "Content-Type": CONTENT_TYPES[extname(filePath)] || "application/octet-stream",
    });
    createReadStream(filePath).pipe(response);
  } catch {
    response.writeHead(404);
    response.end("Not found");
  }
}

/** 按 FRONTEND_PORT 启动前端 HTTP 服务。
 * @returns {void} 启动服务后保持事件循环运行。
 */
function main() {
  const port = Number(process.env.PORT || process.env.FRONTEND_PORT || "8080");
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("PORT 或 FRONTEND_PORT 必须是 1 到 65535 之间的整数");
  }
  createServer((request, response) => {
    void handleRequest(request, response);
  }).listen(port, "0.0.0.0", () => {
    process.stdout.write(`前端服务已启动：http://localhost:${port}\n`);
  });
}

main();
