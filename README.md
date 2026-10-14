# ScreenGuard · 屏鉴

面向公共大屏和视频画面的内容监测与人工审核平台。系统将视频采样、屏幕区域校正、多标签筛查、证据留存、辅助模型建议和风险处置连接为可追溯的工作流程。

## 功能概览

- **视频接入**：本地视频、RTSP、可由后端解码的 HTTP 视频流，以及连接在服务主机上的 USB／内置摄像头；最多同时运行四路。
- **持续监测**：采样截图、屏幕区域标定、断流重连、计划时段、期望运行状态恢复，以及采集和推理状态展示。
- **内容筛查**：支持涉黄／低俗、涉赌、暴力／恐怖、隐私泄露、涉毒／违禁品五类多标签结果；可导入 ONNX 模型包，配置分类阈值、连续确认和事件合并策略。
- **模型接口**：支持本地 llama.cpp，以及 OpenAI Responses、Anthropic Messages 和兼容 Chat Completions 的云端接口；提供手动测试和按视频源配置的自动辅助审核。
- **人工审核**：待审批次、关键帧与上下文、原图／区域图查看、快捷键、审核撤销、操作记录和待处置／处理中／已解决流程。
- **运行管理**：管理员与审核员账号、站内通知、通用 Webhook、证据保留期与配额、数据库迁移及备份恢复。

### 模型与识别范围

内置 `demo-rules` 使用颜色规则演示采集、事件和审核流程，不具备真实风险识别能力。仓库不附带生产模型权重。生产环境需训练或导入经过独立标注数据验证的模型，并完成模型与策略准入后启用自动告警。

大模型结果作为辅助建议记录，不能替代人工结论。模型必须实际支持图片输入；具体模型 ID、可用地区和调用权限以服务商账号为准。自动云端补充默认关闭，启用前需要为对应视频源配置发送许可。

## 环境要求

| 项目 | 要求与用途 |
|---|---|
| Python | 测试基线为 64 位 Python 3.11，推荐使用项目虚拟环境；启动检查要求 3.11 或以上，其他版本不在测试基线内 |
| Windows | 启停脚本依赖 Windows PowerShell；运行账号需具备数据目录读写权限 |
| Web 工作台 | FastAPI、Uvicorn、OpenCV Headless、NumPy、Pydantic、Pillow、HTTPX；依赖清单位于 `src/task2_video_panel/framework_part/requirements.txt` |
| ONNX 推理 | 在线分类器使用 ONNX Runtime CPU 推理；导入时需同时提供 `model.onnx` 和 `manifest.json` |
| 训练与完整回归 | 额外需要 PyTorch、Torchvision、scikit-learn、PyYAML、pytest 等，见 `requirements-dev.txt` |
| 本地视觉大模型（可选） | llama.cpp 的 `llama-server`、支持图片的 GGUF 模型和匹配的 mmproj 投影文件；模型文件需另行准备 |
| 云端模型（可选） | 可访问的 API 地址、有效凭据及账号支持的模型；调用可能产生服务商费用 |
| 摄像头接入（可选） | 可访问的视频流及后端支持的编码；USB 摄像头需连接在运行服务的主机上并授予访问权限 |

基础工作台和 CPU ONNX 推理不强制要求独立显卡。本地大模型的内存、显存和磁盘需求取决于模型规模、量化方式、上下文长度及并发量，应依据选定模型实测；GGUF 文件大小不能直接视为运行内存需求。摄像头路数上限也不代表任意分辨率均可实时处理。

默认疑似证据配额为 20 GiB，保留 7 天，磁盘剩余空间保护阈值为 5 GiB。配额按实际写入占用，并非预先分配；上传视频、模型、数据库、备份和日志需要另外规划空间。已确认风险或手动保留的证据不按疑似样本期限自动删除。

## Windows 快速开始

下列命令使用 **CMD**，并假设已安装 Python 3.11 及 `py` 启动器：

```bat
git clone git@github.com:Symb1sll/ScreenGuard.git
cd ScreenGuard
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r src\task2_video_panel\framework_part\requirements.txt
copy config\runtime_config.example.json config\runtime_config.json
start_windows.bat
```

克隆仓库需要配置 GitHub SSH；也可从仓库页面下载源码。已有源码时从创建虚拟环境开始。已有本机配置时保留该文件，避免覆盖环境设置。

启动器默认使用 `.venv/Scripts/python.exe`。使用已有 Python 环境时，将 `config/runtime_config.json` 的 `python` 改为该解释器路径，并用同一解释器安装依赖。PowerShell 命令及详细配置见[使用说明](docs/使用说明.md)。

工作台默认打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。可上传 `src/task2_video_panel/sample_media/demo_city_screen.mp4` 验证流程。运行 `stop_windows.bat` 停止服务；关闭浏览器不会停止后台采集。

企业账号模式依次使用 `create_enterprise_admin.bat` 和 `start_enterprise.bat`，详见[企业操作说明](docs/企业版使用说明.md)。

## 配置要求

`config/runtime_config.json` 用于本机启动配置，不提交到版本控制。相对路径以项目根目录为基准；同名进程环境变量优先。

| 配置项 | 含义 | 默认值或填写要求 |
|---|---|---|
| `python` | Python 解释器 | `.venv/Scripts/python.exe` |
| `data_dir` | 数据库、证据和上传文件 | `windows_preview/runtime/data`，Windows 启动器要求位于项目内 |
| `state_dir` | 日志、缓存和进程状态 | `windows_preview`，Windows 启动器要求位于项目内 |
| `port` | 工作台端口 | `8765` |
| `model_port` | 本地模型服务端口 | `8088`，需与工作台中连接地址一致 |
| `model_threads` | 本地模型 CPU 线程数 | `4`，按资源与采集延迟调整 |
| `server_path` | llama-server 可执行文件 | 启用本地模型时填写 |
| `model_path` | GGUF 模型文件 | 启用本地模型时填写 |
| `mmproj` | 匹配的视觉投影文件 | 启用本地图片模式时填写 |

基础工作台不要求填写模型文件路径。使用本地视觉模型时，填写模型配置后运行 `start_local_model_vision.bat`，等待服务就绪，再在工作台测试连接；结束时运行 `stop_local_model.bat`。

云端 API 地址、模型 ID、调用次数和密钥在工作台“设置 → 本地与云端模型接口”中配置。Windows 支持当前用户 DPAPI 加密保存密钥；Linux 使用服务环境变量。勿将真实凭据写入示例配置或提交到仓库。接口与自动调用规则见[模型接口说明](docs/MODEL_INTERFACES.md)。

## Linux / WSL2 迁移

后端采用跨平台 Python 实现，可迁移至 Linux／WSL2。Linux 使用 `manage.py` 启动服务，systemd 配置模板位于 `deploy/screen-guard.service.example`；Windows 的 BAT／PowerShell 启动器不用于 Linux 部署。

在已安装 Python 3.11 及 venv 支持的 Linux 环境中，从项目根目录执行：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r src/task2_video_panel/framework_part/requirements.txt
.venv/bin/python -B src/task2_video_panel/manage.py --data-dir ./windows_preview/runtime/data create-admin
.venv/bin/python -B src/task2_video_panel/manage.py --data-dir ./windows_preview/runtime/data serve --accounts --port 8765
```

迁移配置：

1. 按 Linux 目录、用户权限和服务环境调整 systemd 模板；生产服务可使用独立的数据目录。
2. 使用 Linux 版本的 llama.cpp，重新设置模型路径、连接地址和凭据；Windows DPAPI 密钥不能直接在 Linux 解密。
3. 迁移现有数据时执行备份恢复，检查素材路径，重新授权模型调用并手动恢复视频源。
4. 验证编解码支持、网络连通及设备权限；WSL2 的主机摄像头访问需单独配置和验证。
5. 远程使用通过 HTTPS 反向代理接入，并设置实际允许的主机名；应用保持回环监听。

**平台验证范围**：自动回归与启动验证覆盖 Windows；Linux／WSL2 为待验证部署目标。部署验收包括依赖安装、真实 RTSP 接入和连续运行测试。Windows 依赖版本记录仅供参考，不作为 Linux 依赖锁定文件。

## 工程目录与测试

| 目录 | 内容 |
|---|---|
| `src/task2_video_panel/` | 后端、前端静态资源、识别训练与评测、示例素材 |
| `tests/` | 自动回归、模拟视频流、启动和本地模型验收工具 |
| `scripts/` | 启停脚本与工程工具 |
| `deploy/` | 进程守护、systemd 模板和依赖版本参考 |
| `config/` | 配置示例与本机配置 |
| `docs/` | 使用、部署、模型、数据集及开发文档 |
| `windows_preview/` | 默认运行数据和日志，运行时生成 |
| `artifacts/` | 测试和构建产物，执行相关工具时生成 |

完整回归包含训练与 ONNX 导出测试，需要额外安装开发依赖。在已经选定并激活的 Python 环境中执行：

```text
python -m pip install -r requirements-dev.txt
python -X utf8 -B tests/run_tests.py
```

如需 GPU 训练，应按设备环境准备兼容的 PyTorch，再安装其余开发依赖。默认回归使用隔离数据和模拟模型接口，不发起真实云端调用。业务识别指标由独立标注数据和实际场景测试评估。

更多说明：[工程结构](docs/工程结构.md) · [测试入口](tests/README.md) · [训练与评测](docs/WORKBENCH.md) · [数据集规范](docs/DATASET.md) · [离线横评](docs/OFFLINE_BENCHMARK.md)

## 发展规划

| 方向 | 规划内容 |
|---|---|
| 持续运行 | 系统总容量管理、日志轮转、定时备份、模型服务就绪检查与运行守护 |
| 设备与画质 | 黑屏、遮挡、画面冻结、持续模糊及屏幕区域偏移检测 |
| 审核效率 | 相似事件分组、事件时间线、积压预测及可追溯的批量操作 |
| 识别与性能 | 真实数据训练与独立评估、OCR 辅助、变化触发采样；基于性能测试评估 GPU 推理、量化和硬件解码 |
| 模型调用管理 | 费用估算、预算提醒及连续调用失败后的自动暂停机制 |
| 部署与质量 | Windows／Linux 依赖锁定、浏览器自动化测试、版本信息展示、真实摄像头适配及 24／72 小时连续运行测试 |
