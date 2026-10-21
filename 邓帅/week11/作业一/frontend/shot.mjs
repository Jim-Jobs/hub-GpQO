// 联调截图脚本:驱动系统 Chrome 打开 ?demo=1,等 workflow 完成后截图
// 用法: node shot.mjs [url] [输出路径]
import { chromium } from 'playwright-core'

const url = process.argv[2] ?? 'http://127.0.0.1:8001/?demo=1'
const out = process.argv[3] ?? new URL('../workflow.png', import.meta.url).pathname

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } })
await page.goto(url)

// 等待 workflow 终态:汇总卡片(success)或失败告警(failed)
await page
  .waitForSelector('.summary-md, .el-alert--error', { timeout: 120000 })
  .catch(() => console.error('120s 内未出现终态元素'))
await page.waitForTimeout(800) // 等 markdown/表格渲染稳定

await page.screenshot({ path: out, fullPage: true })
console.log('已截图:', out)
await browser.close()
