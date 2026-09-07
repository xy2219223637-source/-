# 视频拆解服务配置

在 `backend/.env`配置视频拆解服务. 修改后重启后端.

## 浏览器插件采集

抖音, 小红书, 快手和 TikTok 页面链接通过 `browser-extension`中的 Chrome Manifest V3 插件采集. 前端创建 10 分钟有效的一次性采集会话, 插件在用户已经登录的平台页面中识别视频, 再把视频上传到后端. 后端写入 OSS 后继续执行现有 `analyze_reference_project`流程.

```env
DIRECTOR_VIDEO_MAX_UPLOAD_MB=300
DIRECTOR_CAPTURE_SESSION_MINUTES=10
```

本地插件安装包由 `browser-extension`目录生成, 前端下载地址为 `/downloads/enrui-video-capture-extension.zip`. 插件面板中的本地应用地址默认为 `http://127.0.0.1:8080`.

插件不会读取或上传平台 Cookie. 采集令牌仅允许向对应项目上传一个视频, 完成后不能重复使用. 生产发布前需要把插件 `manifest.json`中的 `<all_urls>`权限收窄到正式应用域名和支持的平台域名.

## 基础流程

系统按以下顺序执行:

```text
媒体信息探测
-> 场景检测
-> ASR
-> 关键帧提取
-> 多模态逐镜分析
-> 相邻相似镜头合并
-> 视频级结构汇总
```

场景检测只使用真实切镜边界, 不再按固定时长机械拆分. ASR 台词按词级或分段时间戳唯一分配给重叠最大的子分镜, 避免同一句台词重复出现在多个分镜中.

## 推荐配置

当前项目使用火山服务时, 推荐在 `backend/.env`中配置:

```env
LLM_API_BASE=https://ark.cn-beijing.volces.com/api/v3
LLM_API_KEY=<方舟模型推理 API Key>
LLM_VISION_MODEL=<方舟视觉模型 Endpoint ID>

DIRECTOR_ASR_PROVIDER=auto
DIRECTOR_ASR_API_KEY=<豆包语音 API Key>
DIRECTOR_ASR_RESOURCE_ID=volc.seedasr.auc
DIRECTOR_ASR_REQUIRED=false
```

`auto`会根据接口地址选择适配器:

- 方舟地址的 ASR 自动使用 `volcengine_asr`, 调用豆包语音录音文件识别接口.
- 非方舟的 OpenAI-compatible 地址自动使用 `openai_compatible`.
- 显式设置 provider 时, 显式配置优先于自动判断.

方舟模型推理 API Key 与豆包语音 API Key 是两个产品控制台中的凭证, 不能互相替代. 豆包语音 Key 在[豆包语音 API Key 管理](https://console.volcengine.com/speech/new/setting/apikeys?projectName=default)创建. 开通录音文件识别 2.0 后使用资源 ID `volc.seedasr.auc`.

## FFmpeg 和 Docker

优先安装完整 FFmpeg, 保证 `ffprobe`在 `PATH`中. Windows 也可以显式配置:

```env
DIRECTOR_FFMPEG_PATH=C:/ffmpeg/bin/ffmpeg.exe
DIRECTOR_FFPROBE_PATH=C:/ffmpeg/bin/ffprobe.exe
```

系统优先使用 `PATH`中的完整 `ffmpeg`和`ffprobe`, 未找到时才回退到 `imageio-ffmpeg`. 缺少 FFprobe 时, 音轨编码、码率和容器信息可能不完整.

生产和完整视频联调建议使用 Docker. `backend/Dockerfile`已安装 Debian 的完整 FFmpeg, 生产环境可固定为:

```env
DIRECTOR_FFMPEG_PATH=/usr/bin/ffmpeg
DIRECTOR_FFPROBE_PATH=/usr/bin/ffprobe
```

基础镜像默认通过 `docker.m.daocloud.io`拉取, 避免当前网络无法连接 Docker Hub. 如需切回官方镜像, 构建时覆盖 `PYTHON_BASE_IMAGE`、`NODE_BASE_IMAGE`和`NGINX_BASE_IMAGE`即可.

本机执行 `docker compose up -d`时默认读取 `backend/.env`, 不会使用带占位符的生产配置示例.

先基于示例创建 `deploy/.env.production`, 再填写真实配置. 启动时通过 `APP_ENV_FILE`让 Compose 加载该文件:

```bash
cp deploy/.env.production.example deploy/.env.production
APP_ENV_FILE=./deploy/.env.production docker compose build backend
APP_ENV_FILE=./deploy/.env.production docker compose up -d backend frontend
```

本机前端开发仍可使用 Vite, 后端视频处理放进 Docker, 这样 FFmpeg 版本和 Linux 运行环境更稳定.

## ASR 方案

### 方案 A: 火山豆包语音录音文件识别

这是当前项目的默认方案. 系统将 FFmpeg 提取的 MP3 临时上传到现有 OSS, 生成 15 分钟签名 URL, 提交 ASR 后轮询结果, 最后删除临时音频对象. 返回的 `utterances.start_time`和`end_time`会直接映射为毫秒级台词时间戳.

```env
DIRECTOR_ASR_PROVIDER=auto
DIRECTOR_ASR_API_BASE=
DIRECTOR_ASR_API_KEY=<豆包语音 API Key>
DIRECTOR_ASR_RESOURCE_ID=volc.seedasr.auc
DIRECTOR_ASR_LANGUAGE=zh
DIRECTOR_ASR_POLL_INTERVAL_SECONDS=1.5
DIRECTOR_ASR_POLL_TIMEOUT_SECONDS=180
DIRECTOR_ASR_REQUIRED=false
```

`DIRECTOR_ASR_API_BASE`留空时使用官方地址 `https://openspeech.bytedance.com/api/v3/auc/bigmodel`. 优点: 中文识别效果和国内访问更适合当前项目, 支持分句时间戳. 缺点: 必须开通豆包语音服务并单独创建语音 API Key, 输入音频需要可访问 URL.

### 方案 B: OpenAI-compatible 转写接口

接口必须支持 `POST /audio/transcriptions`和 `verbose_json`分段时间戳.

```env
DIRECTOR_ASR_PROVIDER=openai_compatible
DIRECTOR_ASR_API_BASE=https://api.openai.com/v1
DIRECTOR_ASR_API_KEY=sk-...
DIRECTOR_ASR_MODEL=whisper-1
DIRECTOR_ASR_LANGUAGE=zh
DIRECTOR_ASR_REQUIRED=false
```

优点: 接入简单, 时间戳格式统一, 运维成本低. 缺点: 有调用费用, 国内网络和数据合规需要单独评估. 不是所有 OpenAI-compatible 对话接口都支持音频转写.

### 方案 C: 本地 faster-whisper

```bash
backend/.venv/Scripts/python.exe -m pip install "faster-whisper>=1.1,<2"
```

```env
DIRECTOR_ASR_PROVIDER=faster_whisper
DIRECTOR_ASR_MODEL=large-v3
DIRECTOR_ASR_LANGUAGE=zh
DIRECTOR_ASR_LOCAL_DEVICE=auto
DIRECTOR_ASR_LOCAL_COMPUTE_TYPE=int8
DIRECTOR_ASR_REQUIRED=false
```

优点: 音频不出本地, 无按次 API 费用, 支持离线运行. 缺点: 首次下载模型, CPU 推理较慢, `large-v3`更适合 GPU. CPU 可先用 `small`或 `medium`.

## 失败策略

默认配置允许 ASR 失败后继续完成画面拆解, 并在结果页显示警告. 要求 ASR 失败时终止任务, 配置:

```env
DIRECTOR_ASR_REQUIRED=true
```
