# 网站身份统一实施记录

实施日期: 2026-10-13.

## 1. 实施范围

- 将入口默认标题和所有路由标题统一为公司全称. 首页仅显示公司全称, 功能页使用固定功能名称及公司全称.
- 将路由标题集中到 `frontend/src/lib/routeTitles.ts`, 由 `RouteTitle` 在 `BrowserRouter` 内统一更新. 放在懒加载边界之外, 覆盖登录页, 修改密码页和页面加载期间.
- 按用户后续确认, 桌面及移动展开侧栏仅展示指定 Logo, 不再附加公司名称文字. 登录页和移动端顶部保留公司全称, 保留原说明, 表单, 导航和页面布局.
- 为长名称采用自动均衡换行, 为移动导航按钮保留固定宽度.
- 不新增图片生成或视频生成路由. 不修改商品资料, 品牌视觉, 知识库内容, 权限或登录流程.
- 原样复制用户指定的 PNG 到 `frontend/src/assets/company-logo.png`, 仅替换侧栏 Logo. 按后续确认, 将 favicon 改为 ArtInjecter 对应的字母 A 方形标识. 不提交或发布代码.

## 2. 全量路由标题对照

以下 44 项包含 40 个页面路径, 3 个历史重定向入口及 1 个未知路径规则. `:id` 不进入标题, 用户输入的项目名称也不进入标题.

| 路由 | 标签页标题 |
| --- | --- |
| `/` | 杭州美势文化发展有限公司 |
| `/login` | 登录 - 杭州美势文化发展有限公司 |
| `/change-password` | 修改密码 - 杭州美势文化发展有限公司 |
| `/products/new` | 创建商品 - 杭州美势文化发展有限公司 |
| `/products` | 商品管理 - 杭州美势文化发展有限公司 |
| `/products/:id` | 商品详情 - 杭州美势文化发展有限公司 |
| `/knowledge` | 知识库 - 杭州美势文化发展有限公司 |
| `/design-skills` | 设计 Skill - 杭州美势文化发展有限公司 |
| `/brand-visuals` | 品牌视觉 - 杭州美势文化发展有限公司 |
| `/task-center` | 任务与费用 - 杭州美势文化发展有限公司 |
| `/detail-templates` | 详情页模板 - 杭州美势文化发展有限公司 |
| `/production` | 批量生产 - 杭州美势文化发展有限公司 |
| `/quality` | 质量中心 - 杭州美势文化发展有限公司 |
| `/creative-projects` | AI 创作工作台 - 杭州美势文化发展有限公司 |
| `/creative-projects/:id` | 创意画布 - 杭州美势文化发展有限公司 |
| `/creative-projects/:id/storyboard` | 详情页编排 - 杭州美势文化发展有限公司 |
| `/video-projects` | AI 口播视频 - 杭州美势文化发展有限公司 |
| `/video-projects/:id` | 口播视频编辑 - 杭州美势文化发展有限公司 |
| `/director-inspiration` | 编导灵感 - 杭州美势文化发展有限公司 |
| `/director-inspiration/:id` | 编导分析 - 杭州美势文化发展有限公司 |
| `/director-inspiration/:id/workspace` | 编导工作台 - 杭州美势文化发展有限公司 |
| `/shooting-tasks` | 拍摄任务 - 杭州美势文化发展有限公司 |
| `/semi-finished-assets` | 半成品素材库 - 杭州美势文化发展有限公司 |
| `/semi-finished-assets/:id` | 半成品素材详情 - 杭州美势文化发展有限公司 |
| `/finished-video-assets` | 成品素材库 - 杭州美势文化发展有限公司 |
| `/finished-video-assets/:id` | 成品素材详情 - 杭州美势文化发展有限公司 |
| `/final-videos` | 成片库 - 杭州美势文化发展有限公司 |
| `/final-videos/:id` | 成片详情 - 杭州美势文化发展有限公司 |
| `/qianchuan/oauth/result` | 千川授权结果 - 杭州美势文化发展有限公司 |
| `/manual-video-compose` | 视频编排 - 杭州美势文化发展有限公司 |
| `/manual-video-compose/:id` | 视频编排编辑 - 杭州美势文化发展有限公司 |
| `/platform-admin` | 平台管理后台 - 杭州美势文化发展有限公司 |
| `/tenant-admin` | 企业管理后台 - 杭州美势文化发展有限公司 |
| `/tenant-admin/roles` | 角色与权限 - 杭州美势文化发展有限公司 |
| `/tenant-admin/taxonomy` | 视频分类管理 - 杭州美势文化发展有限公司 |
| `/smart-video-compose` | 智能成片 - 杭州美势文化发展有限公司 |
| `/smart-video-compose/:id` | 智能成片编辑 - 杭州美势文化发展有限公司 |
| `/finished-video-remix` | 成品混剪 - 杭州美势文化发展有限公司 |
| `/finished-video-remix/:id` | 成品混剪编辑 - 杭州美势文化发展有限公司 |
| `/performance-video-projects/:id` | 视频自动剪辑 - 杭州美势文化发展有限公司 |
| `/video-taxonomy` | 视频分类管理 - 杭州美势文化发展有限公司 |
| `/performance-video-projects` | 智能成片 - 杭州美势文化发展有限公司 |
| `/video-assets` | 半成品素材库 - 杭州美势文化发展有限公司 |
| `*` | 杭州美势文化发展有限公司 |

重定向规则保持不变:

| 入口 | 最终页面 |
| --- | --- |
| `/video-taxonomy` | `/tenant-admin/taxonomy` |
| `/performance-video-projects` | `/smart-video-compose` |
| `/video-assets` | `/semi-finished-assets` |
| 未知路径 | `/` |

上述入口预先使用最终页面的标题. 如果未登录而继续跳转到登录页, 则使用登录页标题. `/performance-video-projects/:id` 当前是独立兼容编辑入口, 不是重定向, 使用固定的"视频自动剪辑"名称.

## 3. 品牌标识确认清单

| 项目 | 状态 | 后续要求 |
| --- | --- | --- |
| 公司名称 | 已实施 | 始终使用公司全称, 不采用简称 |
| 页面展示位置 | 已按补充要求调整 | 侧栏仅展示 Logo. 登录页和移动顶部保留公司全称 |
| 指定原图 | 用户已确认使用 | 使用原始 2000x468 PNG, 不重绘, 不裁切, 不修改图像内容 |
| 桌面及移动侧栏 Logo | 已替换 | 自适应宽度, 高度按比例缩放, 替代文本为公司全称及 Logo |
| 登录页 Logo | 本次不调整 | 当前独立显示公司全称, 表单及说明保持不变 |
| 标签页图标 | 已按补充要求替换 | 使用白底黑色字母 A 方形 SVG, 已检查 16x16 和 32x32 的浅色及深色背景. 入口资源 URL 带 `v=artinjecter-1` 缓存版本 |

侧栏补充要求只针对标注的品牌区域, 后续另行授权替换标签页图标. 浏览器实际渲染可辨识原图文字为 ArtInjecter, 未根据附件预览重绘图片. 复制前后的文件内容完全一致. favicon 使用 SVG 路径表示首字母 A, 不依赖本机字体, 不将完整横向 Logo 缩小. 是否满足备案审核仍需审核方确认.

## 4. 旧名称核查

检索 `DLR`, `Dirovo`, `dirovo` 和 `蒂洛薇`, 未执行全局替换.

| 位置 | 分类与处置 |
| --- | --- |
| `frontend/index.html` | 默认旧标题已替换为公司全称 |
| `frontend/src/pages/Login.tsx` | 登录品牌区域旧名称已替换 |
| `frontend/src/components/Layout.tsx` | 侧栏已改用指定 PNG 及新的 alt, 移动顶部继续使用公司全称 |
| `frontend/src/assets/dirovo-logo.svg` | 旧图文件保留, 当前已无页面引用, 未执行无关删除 |
| `frontend/public/favicon.svg` | 已将原字母 D 图标替换为对应 ArtInjecter 的字母 A 标识 |
| `frontend/tests/site-identity.test.tsx` | 断言旧登录名称已移除, 侧栏仅显示指定 Logo, 不是网站展示 |
| `backend/tests/test_storage_cache.py` | DLR 用作测试参数, 不是网站身份, 保持不变 |
| `docs/legal/00_使用说明与待确认清单.md` | 原法律材料的待确认历史说明, 本次不修改. 对外使用前应重新核对品牌关系 |

## 5. 验证结果及边界

- 新增 58 项测试全部通过, 包含全量路由标题, 路由清单漂移检查, 默认 HTML 标题, 静态与动态路径优先级, 查询参数隔离, 重定向, 懒加载, 导航历史及 favicon 资源版本与格式检查.
- 真实 App 挂载测试覆盖登录页与修改密码页, 不依赖业务 Layout. 登录字段与说明仍保留, 移动导航打开和关闭测试通过.
- 全部 Vitest 测试共 66 项通过. 既有 Node 测试共 5 项通过. TypeScript 的 app 和 node 配置检查通过.
- Vite 生产构建通过. 既有 canvas vendor 包超过 500 kB 的提示仍存在, 本次不修改无关分包. 构建与预览产物放在系统临时目录, 未重建浏览器扩展资源.
- 真实浏览器检查登录页和修改密码页的直接访问, 刷新, 前进及后退, 标题正确. 桌面视口 1440x960 和移动视口 320x740 下, 公司名称没有水平溢出.
- 最新侧栏 Logo 截图来自用户当前已登录的真实首页. 验证桌面侧栏和移动展开导航均加载指定原图, 公司名称未附加到侧栏, 导航打开和关闭正常. Logo 自然尺寸为 2000x468, 桌面显示尺寸约为 199.33x46.64, 比例一致.
- 尚未进行真实账号登录后的全业务页面端到端验证, 未执行密码修改或千川授权. 业务路由标题由全量自动测试覆盖, 完整业务回归仍需有效测试账号及后端服务.
- 指定 Logo 已在真实浏览器确认正常显示. favicon 在 16x16 和 32x32 的浅深背景下正常加载和显示, 实际页面刷新后引用带新版本的 URL, 本地资源响应为正确 SVG. 尚未验证生产 CDN 缓存, 未进行部署或备案审核.

验证命令在 `frontend` 目录执行:

```cmd
node node_modules\vitest\vitest.mjs run --config vitest.config.ts
node --test tests\loginReturn.test.cjs tests\notificationStatus.test.cjs
node node_modules\typescript\bin\tsc -p tsconfig.app.json
node node_modules\typescript\bin\tsc -p tsconfig.node.json --noEmit --incremental --tsBuildInfoFile %TEMP%\enrui-site-identity-node.tsbuildinfo
node node_modules\vite\bin\vite.js build --outDir %TEMP%\enrui-site-identity-build-final-20261013
```

可选: 设置 `SITE_IDENTITY_VISUAL_DIR` 为系统临时目录中的专用子目录后运行新增测试, 可导出只读 Layout 校验 HTML. 默认测试不输出夹具文件. 夹具引用本地 5173 开发服务的实际样式和当前 Logo, 不作为生产页面.

## 6. 验证截图

| 截图 | 来源与视口 |
| --- | --- |
| [桌面登录页](site-identity-screenshots/login-desktop.jpg) | 真实页面, 1440x960 |
| [移动登录页](site-identity-screenshots/login-mobile.jpg) | 真实页面, 320x740 |
| [移动修改密码页](site-identity-screenshots/change-password-mobile.jpg) | 真实页面, 320x740 |
| [桌面侧栏 Logo](site-identity-screenshots/sidebar-logo-desktop.jpg) | 真实首页, 截取侧栏与页面顶部 |
| [移动顶部](site-identity-screenshots/header-mobile-fixture.jpg) | Layout 静态校验夹具, 320x740 |
| [移动展开侧栏 Logo](site-identity-screenshots/sidebar-logo-mobile.jpg) | 真实首页, 320x740 视口的顶部区域 |
| [标签页图标尺寸检查](site-identity-screenshots/favicon-sizes.jpg) | 临时校验页, 使用真实 favicon 资源, 16x16 和 32x32 的浅深背景 |

此前的 `sidebar-desktop-fixture.jpg` 和 `sidebar-mobile-fixture.jpg` 保留为首次实施的历史截图, 不作为当前 Logo 验收依据.
