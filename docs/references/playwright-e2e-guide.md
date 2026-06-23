# Playwright E2E 测试诊断指南

技术选择：**Playwright** 是固定的 e2e 测试框架，不要引入替代方案。

## 为什么 E2E 诊断成本高

对 AI 辅助 e2e 会话的历史分析表明，有三个主要耗时点：

| 模式 | 根因 |
| ---- | ---- |
| **盲目重试** - 对同一个失败测试反复重跑，却不调查原因 | 把重跑当成了调查 |
| **超时循环** - 45 秒超时 → 重跑 → 再次超时 | 是异步渲染竞态，没有被检查 |
| **把全量套件用于局部变更** - 改了一个 widget 却跑所有测试 | 没有有针对性的验证策略 |

**关键原则**：先检查，再重试。一个 2 分钟的诊断脚本，能省下 30 分钟的盲目重跑。

---

## 诊断决策树

当测试失败时，按这个顺序走：

```text
1. 开发服务器还活着吗？
   NO -> 启动它。检查端口冲突。再跑一次。
   YES -> 继续

2. 目标元素是否真的出现在 DOM 里？
   NO -> 组件没有渲染
       -> 检查浏览器控制台错误
       -> 检查组件注册 / 导入
       -> 检查路由：URL 是否真的解析到了预期页面？
   YES -> 元素存在，但状态不对
       -> 检查 getComputedStyle 里的 display:none / visibility:hidden
       -> 检查异步数据是否已加载（网络面板 / waitForResponse）
       -> 检查 CSS 类是否被生成（Tailwind / 构建管线）

3. 失败是稳定复现还是偶发？
   稳定复现 -> 代码或测试 bug。修复后再跑一次。
   偶发（不改代码重跑又过了）
       -> 检查竞态（用 waitForSelector / waitForResponse 替代 waitForTimeout）
       -> 检查测试状态泄漏（测试之间的全局状态）
       -> 检查开发服务器缓存是否过旧（重启服务器）
```

---

## 内联诊断脚本模板

不要为了诊断而创建临时 `.spec.ts` 文件。请使用内联脚本：

```bash
pnpm exec node -e "const { chromium } = require('@playwright/test'); (async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
  page.on('pageerror', err => errors.push(err.message));
  await page.goto('http://127.0.0.1:<PORT>/<PATH>', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(2000);
  const text = await page.textContent('body');
  console.log(JSON.stringify({ errors, text: text?.substring(0, 500) }, null, 2));
  await browser.close();
})();"
```

按你的项目替换 `<PORT>` 和 `<PATH>`。需要时可以加入项目特定的运行时检查（debugger API、状态 dump 等）。

---

## 分层验证策略

**不要因为修一个单点就直接跑全量套件。** 按这个升级路线来：

```text
Level 0: 单个失败行
  playwright test "path/to/test.spec.ts:LINE" --workers=1

Level 1: 单个 spec 文件
  playwright test "path/to/test.spec.ts" --workers=1

Level 2: 相关 spec 文件
  playwright test tests/e2e/<feature-area>/*.spec.ts --workers=1

Level 3: 全量套件（仅在 commit / PR 前）
  playwright test
```

| 变更范围 | 验证层级 |
| -------- | -------- |
| 单个组件 / 页面 | Level 0 -> Level 1 |
| 共享基础设施（路由、状态、认证） | Level 1 -> Level 2 |
| CSS / 构建管线 / 配置 | Level 1 -> Level 2 |
| 提交或发 PR 前 | Level 3 |

## 常见失败模式

### 超时 / 元素不可见

**症状**：`toBeVisible()` 失败，`Test timeout of Xms exceeded`

**根因（按可能性排序）**：

| 原因 | 如何识别 | 修复 |
| ---- | -------- | ---- |
| **异步数据尚未加载** | 查看网络面板；元素在，但内容还是占位符 | `await page.waitForResponse(...)`，或者 `await expect(locator).toBeVisible({ timeout: 10000 })` |
| **竞态条件** | 不改代码重跑又过了 | 用明确等待替换 `waitForTimeout(N)` |
| **CSS 类没有生成** | 元素在 DOM 里，也应用了类，但 `getComputedStyle` 显示没效果 | 检查构建管线（Tailwind `@source`、PostCSS 配置等） |
| **组件没有渲染** | 目标元素根本不在 DOM 中 | 检查控制台错误；验证组件注册 / 路由 |

### 端口冲突

**症状**：`Error: Port XXXX is already in use`

**原因**：上一个开发服务器还在运行。

**修复**：在启动测试前杀掉残留服务器进程。不要通过递增端口号来逃避冲突，这会留下孤儿进程。

```bash
# Linux / macOS
lsof -ti :4173 | xargs kill -9 2>/dev/null

# Windows
for /f "tokens=5" %a in ('netstat -aon ^| findstr ":4173"') do taskkill /F /PID %a 2>nul
```

在 `playwright.config.ts` 里设置 `reuseExistingServer: !process.env.CI`，这样本地运行会复用已经在跑的服务器。

### 偶发 / 间歇性失败

**症状**：同一个测试有时过、有时不过。改端口“解决了”问题。

| 原因 | 证据 | 修复 |
| ---- | ---- | ---- |
| **开发服务器 / 缓存过旧** | 重启服务器后好了 | 杀掉并重启；必要时清理构建缓存 |
| **竞态条件** | 第一次失败，立刻重跑通过 | 为异步依赖增加显式等待 |
| **测试顺序依赖** | 单跑通过，整套失败 | 用 `--workers=1` 运行；检查 `beforeAll` / `afterAll` 里的全局状态 |

### 测试状态泄漏

**症状**：`--workers=1` 时通过，但并行 worker 下失败；或者单独运行通过，整套运行失败。

**修复**：确保每个测试都导航到干净页面。不要依赖 `beforeAll` 里设置的全局状态，而后续测试又必须依赖它。每个测试都应能独立运行。

## 反模式

1. **盲目重试循环**：对同一个失败测试重跑超过 2 次却不做调查。每次重试都要消耗服务器启动时间和测试执行时间，一整个会话里的浪费可能超过数小时。

2. **端口升级**：为避开冲突不断加端口号，留下 N 个孤儿开发服务器。应该杀掉残留服务器。

3. **把全量套件用于局部变更**：改了一个组件就运行 `playwright test`（全部 spec）。应先运行受影响的 spec。

4. **基于截图调试**：只看 Playwright 失败截图来猜原因。应使用 `page.evaluate()`、`page.locator().innerHTML()` 和 `getComputedStyle` 做程序化检查。

5. **创建临时诊断 spec**：写 `tmp-*.spec.ts` 或 `diag-*.spec.ts`，然后又从不清理。一次性诊断请用内联 `node -e` 脚本。

## Playwright 配置基线

推荐的 `playwright.config.ts` 结构：

```typescript
import { defineConfig, devices } from '@playwright/test';

const port = parseInt(process.env.PLAYWRIGHT_PORT || '4173', 10);
const baseUrl = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 45_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  workers: 2,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: baseUrl,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `pnpm dev --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseUrl,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
```

关键决策：

- `retries: 0` - flaky 测试应该被修掉，而不是靠重试掩盖
- `trace: 'retain-on-failure'` - 只在失败时保留 trace，让 CI 保持快
- `PLAYWRIGHT_PORT` 环境变量 - 调试时可以切换端口
- `reuseExistingServer: !process.env.CI` - 本地复用服务器，CI 中使用全新服务器

## 推荐诊断流程

```text
1. 测试失败
   -> 2. 检查（内联 node -e 脚本，不要新建 .spec.ts）
   -> 3. 分类：代码 bug | 测试 bug | 环境偶发性
   -> 4. 修复
   -> 5. 验证：单个测试行（Level 0）
   -> 6. 扩展：整个 spec 文件（Level 1）
   -> 7. 如果是基础设施变更：相关 spec（Level 2）
   -> 8. 只有在提交前才跑全量套件（Level 3）
```

## 复制清单

复制这个模板后，请定制：

- [ ] `playwright.config.ts` 和所有脚本里的端口号
- [ ] `webServer.command` 中的开发服务器启动命令
- [ ] `testDir` 路径
- [ ] 添加项目特定的运行时检查钩子（debugger API、状态 dump）
- [ ] 给 Common Failure Patterns 部分添加项目特定失败模式
- [ ] 给本地附录添加项目特定测试文件及其已知问题
