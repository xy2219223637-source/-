# 恩睿视频解析助手

## 安装与使用

1. 从编导灵感下载插件 ZIP, 解压到固定目录.
2. 使用 Chrome 或 Edge 111 及以上版本, 打开 `chrome://extensions`或 `edge://extensions`, 开启开发者模式并加载包含 `manifest.json`的目录.
3. 允许插件访问应用和目标视频平台, 在同一浏览器配置文件中登录平台.
4. 在编导灵感粘贴视频分享链接, 点击解析. 插件自动在后台打开页面, 获取并上传视频; 后端验证通过后自动分析. 无需点击插件图标.
5. 需要登录或验证时, 在失败页面点击"打开原视频", 完成后点击"重新采集". 成功后默认关闭临时页面, 失败时保留页面供处理.

更新时用新包覆盖原插件目录, 在扩展管理页点击重新加载, 再刷新应用页面. 前端核对版本和构建标识; 仅版本号相同但代码不同也会提示更新. 插件面板只保留应用入口, 版本信息和成功后自动关闭页面设置.

## 采集范围

- 优先适配抖音. 标准视频链接和 `modal_id`链接按视频 ID 匹配, 短链接跳转后提取 ID.
- 从页面结构化状态和浏览器原生详情响应提取目标视频播放地址. 播放器必须具有匹配的项目 ID, 不使用无法关联到目标视频的广告, 推荐或网络媒体响应.
- 优先在原页面请求视频, 失败时尝试扩展请求. 发现无效地址后继续等待有效资源, 下载完成后检查文件签名和大小, 后端进一步校验视频轨道.
- 保留小红书, 快手, TikTok 和普通 HTML5 页面基础采集, 这些平台尚未实现抖音同等级别的结构化 ID 匹配和专项验收.
- 支持可直接下载的 MP4 和 WebM 容器, 包括兼容 MP4 容器的 MOV/M4V. 不合并 HLS/DASH 分段流, 不处理 DRM.
- 使用各自浏览器会话, 不读取或上传 Cookie, 不共享个人凭证. 平台登录, 验证, 站点访问权限和网络仍可能影响成功率.
- 诊断只保留版本, 构建标识, 目标 ID, 域名, 地址摘要, HTTP 状态, MIME 和大小, 不上传媒体签名地址或响应正文.

## 构建与验证

在 `frontend`目录执行:

```sh
npm ci
npm run build
```

构建从本目录源码生成 `build-info.js`, 页面环境独立脚本 `page-capture-main.js`, 前端版本元数据和 `frontend/public/downloads/enrui-video-capture-extension.zip`, 并校验 ZIP 内容. 修改页面采集逻辑时编辑 `page-capture.js`, 不直接编辑生成文件. 单独更新包时执行 `npm run build:extension`. 同步发布前端, 后端和下载包.

在仓库根目录执行:

```sh
node --test browser-extension/tests/capture-utils.test.js browser-extension/tests/capture-download.test.js
node --test browser-extension/tests/extension-browser.cjs
```

浏览器集成测试需要已安装 Playwright 和 Chromium. 可通过 `PLAYWRIGHT_MODULE`指定现有 Playwright 模块路径. 测试使用临时浏览器配置, 模拟平台响应和预留的源标签页, 阻断外部网络, 需要空闲的本地 `8080`或 `5173`端口; 不使用真实账号, 不访问生产采集服务. Playwright 对插件新建页面的首次导航拦截不稳定, 因此真实新建标签页流程保留为人工验收项. 后端测试在 `backend`目录使用项目 Python 环境运行 `python -m unittest tests.test_director_workflow`.

发布验收时, 在不同用户的 Chrome/Edge 中对同一个抖音链接检查自动采集成功, 结果属于指定视频, 登录或验证提示, 失败重试和版本过期提示. 模拟测试不能替代真实平台验收.

## 部署配置

默认应用地址为 `https://waynepartners.xyz`. 本地受信任来源为 `http://localhost:8080`, `http://127.0.0.1:8080`, `http://localhost:5173`和 `http://127.0.0.1:5173`. 新增部署来源时修改 `capture-utils.js`后重新构建.

当前保留 `<all_urls>`以兼容分享跳转和视频 CDN. 缩小权限时同时登记应用, 平台跳转和实际视频 CDN, 并验证页面请求及扩展请求.
