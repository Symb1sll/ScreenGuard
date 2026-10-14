# 本地与云端模型接口

本模块提供手动测试、人工辅助复核和每路自动辅助审核。连接入口在工作台“设置”和“素材与模型”，自动调用策略在监控卡片的“设备与自动审核设置”。

## 使用入口

1. 双击项目根目录的 **start_windows.bat**，打开 http://127.0.0.1:8765。
2. 使用本地图片识别时双击 **start_local_model_vision.bat**；纯文本模式使用 **start_local_model.bat**。加载耗时取决于模型和设备，等待服务就绪后再测试连接。切换模式前先运行 stop_local_model.bat。
3. **设置 → 本地与云端模型接口**：新增连接，选择提供商，填写账号实际可用的模型 ID 和 API 密钥，然后保存。可以读取服务的模型列表；部分服务不提供此接口，允许手动填写。
4. **素材与模型 → API 模型测试**：先做文本连通测试，再测试待审核文本；有视觉能力的模型可以上传图片测试。每次发送云端输入前需勾选允许发送。
5. **人工复核 → 当前画面 → AI 辅助复核**：发送当前正在查看的这一帧，可查看本事件历史调用。AI 建议不会覆盖人工标签、备注、原 ONNX 分数或触发自动告警。
6. 不使用本地模型时双击 **stop_local_model.bat** 释放其资源。工作台和模型服务分别启停。

模型 ID 不写死成“最新型号”：各账号开通的模型不同，填写提供商控制台或模型列表中的实际 ID。模型列表不表示图片能力；确认具体模型支持图片后再开启。JSON 输出选项仅在目标服务支持时启用，本机 llama.cpp 默认启用。

## 本地 Qwen

在 `config/runtime_config.json` 中填写 `server_path`、`model_path`、`mmproj`，分别对应 llama-server、GGUF 模型及匹配的视觉投影文件。模型资产需自行准备，仓库不包含模型权重。

- 默认基础地址为 `http://127.0.0.1:8088/v1`，服务模型别名为 `qwen-local`，工作台连接 ID 为 `local-qwen`。
- Windows 启动器使用 4096 上下文、1 个推理槽、默认 4 个 CPU 线程，并关闭 RAM 提示词缓存。图片模式限制图像 token 为 1024–1536；实际兼容性需通过图片调用验证。
- 日志、缓存和进程状态保存在项目 `windows_preview/local_model`；模型和服务程序作为只读输入使用。
- 启动器生成本地 API 密钥，使用当前 Windows 用户 DPAPI 加密存储，通过进程环境变量传给 llama.cpp。
- llama.cpp 仅监听回环地址，关闭自带 Web UI；工作台后端携带密钥访问服务。
- Python 解释器由本机配置决定，默认 `.venv/Scripts/python.exe`。更改模型端口时，同步修改工作台连接地址。
- “支持图片输入”开关声明连接能力；切换文本／视觉服务模式前，先停止原模型服务并重新启动。

本地多模态需要模型与匹配的投影文件，llama.cpp 提供 OpenAI 兼容接口。[llama.cpp 多模态说明](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)

## 提供商与协议

| 提供商 | 协议 | 默认基础地址 |
|---|---|---|
| GPT / OpenAI | Responses | https://api.openai.com/v1 |
| Claude / Anthropic | Messages | https://api.anthropic.com/v1 |
| DeepSeek | Chat Completions | https://api.deepseek.com/v1 |
| Kimi / Moonshot | Chat Completions | https://api.moonshot.cn/v1 |
| GLM / 智谱 | Chat Completions | https://open.bigmodel.cn/api/paas/v4 |
| 本地 llama.cpp | Chat Completions | http://127.0.0.1:8088/v1 |
| 其他兼容服务 | 可选上述协议 | 手动填写 |

GPT 使用文本/图片 input blocks，默认 `store=false`；Claude 使用原生消息与 base64 图片块；其余使用兼容消息体。具体账户、地区地址、模型和图片能力仍需实际验证。[OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)、[Claude 图片接口](https://platform.claude.com/docs/en/build-with-claude/vision)、[DeepSeek 图片接口](https://api-docs.deepseek.com/guides/vision/)、[Kimi 官方模型说明](https://huggingface.co/moonshotai/Kimi-K2.5)、[智谱官方示例](https://github.com/zai-org/GLM-V/blob/main/skills/glmv-prompt-gen/scripts/prompt_gen.py)

不需要各家 Python SDK，统一使用 httpx，已加入框架 requirements。当前不继承 HTTP_PROXY / HTTPS_PROXY，也不跟随重定向。需要代理时可使用可直连的授权 API 网关，或后续增加专门的代理设置。

## 调用与结果规则

- 最多同时调用 2 个请求，包含排队在内最多 6 个任务；本地 llama.cpp 自身使用 1 个推理槽。
- 每个连接的每日请求次数上限按 **UTC 00:00，即北京时间 08:00** 重置。连通测试、文本、图片和失败调用均计入；模型列表读取不计入生成次数。
- 修改连接版本不重置配额。这是次数上限，不是金额上限；不同连接分别计数，实际费用仍需在提供商侧设置限额。
- 相同 request_id 与相同内容返回同一记录，不重复发送；同标识不同内容返回 409。没有自动重试或自动切换提供商。
- 上述规则适用于单次请求。每路自动调度可在本地调用不确定或失败后，按预先保存的发送许可补充一次指定云端请求；这是独立且可追踪的调用，不会把失败解释为正常。
- 服务停止时取消尚未发送的排队任务；已发送的请求可能仍在提供商处理。重启后未完成请求标记为中断/未知，不自动补发。
- 超时、限流、拒绝、截断、非 JSON、漏类别、重复键、NaN、越界分数均不会变成“正常”或默认 0 分。
- 必须完整返回五类：sexual_vulgar、gambling、drugs_contraband、violence_terror、privacy_leak。分数是未经业务校准的模型建议。
- 本地 llama.cpp 开启 JSON 输出时，发送五类结果的 JSON Schema，并设 temperature=0；连通测试使用独立的 ready 格式。后端仍严格校验结果，格式失败记为未知。此约束只用于本地 Chat 接口，云端请求格式保持原协议。[llama.cpp 结构化输出](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- 图片支持 JPEG / PNG / WebP，最大 8 MiB / 2000 万像素；发送前转为最大边长 1280 的 JPEG，清除 EXIF 元信息。已有证据先校验 SHA-256。
- 原始测试文本、上传图片不新增落盘副本。记录保存输入摘要、模型/连接/提示词版本、事件/证据引用、状态、生成的建议摘要和 token 用量。建议可能概述输入内容，与审核摘要一样保留，不保证模型一定遵守脱敏提示。已有原图仍按工作台证据留存设置处理。
- 前端不把密钥写入 localStorage/sessionStorage；设置 API 仅返回是否已配置。密钥输入在保存请求结束后清空。直接保存的密钥优先于环境变量。
- 改变已保存连接的目的地址、协议或密钥环境变量时，需要重新填写密钥或显式清除旧密钥，防止旧密钥随配置变更发往其他服务。

## HTTP 接口

自动审核默认关闭。管理员选定本地视觉连接后逐路启用，事件固定后选择峰值区域证据提交。最多 64 个待处理任务，同一连接自动请求串行并避让正在运行的手动请求；重复证据去重，队列过期后保留人工复核。每路额度按设置时区计算，连接额度仍按 UTC 计算。自动请求不改变原分类器分数、人工结论或风险告警准入。

自动任务固定连接版本、每路配置版本及证据内容版本。版本变化或许可撤回阻止继续提交；已经发送的请求无法撤回。重启后关联已预留调用记录，结果不明的请求不自动补发。恢复备份时关闭自动调用。

自动审核后台只调用已运行的模型服务。Windows 先运行图片模型启动器，等待就绪，再开始采集。`config/runtime_config.json` 可配置外部路径和模型 CPU 线程数；默认 4，具体并发能力仍应实测。

所有接口继承工作台会话校验；账号模式先登录。先 GET /api/bootstrap 获得 HttpOnly cookie 和 csrf，写操作同时发送 X-SG-CSRF。提供商 API key 不是工作台会话令牌。

| 方法 | 地址 | 作用 |
|---|---|---|
| GET | /api/ai/providers | 提供商预设和本地文件信息 |
| GET | /api/ai/connections | 连接、版本、密钥状态、当日用量 |
| PUT | /api/ai/connections/{id} | 新增/更新连接，带 expected_version |
| GET | /api/ai/connections/{id}/history | 最近 50 个无密钥的配置版本 |
| POST | /api/ai/connections/{id}/models?expected_version=N | 读取保存的连接的模型列表 |
| POST | /api/ai/requests | 提交文本、连通测试或已有证据，202 返回记录 |
| POST | /api/ai/requests/image | multipart：metadata JSON 和 file |
| GET | /api/ai/requests/{id} | 查询进度/结果 |
| GET | /api/ai/requests?event_id=N&limit=20 | 最近调用记录，event_id 可省略 |
| GET / PUT | /api/sources/{id}/profile | 每路配置、版本和自动审核许可 |
| GET | /api/automatic-review | 自动队列计数及最近任务 |
| GET | /api/events/{id} | 固定证据、automatic_review 与调用结果 |

保存体包括 expected_version（新建为 0）、config（与 GET 返回一致），可选 api_key / clear_key。ID 允许小写字母、数字、连字符和下划线，最多 64 字符。最多 20 个连接，停用使用 enabled=false。

下面的示例使用已有本地配置，产生一次文本调用。请保存在项目目录运行：

```python
import time, uuid, httpx

with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False) as client:
    client.headers["X-SG-CSRF"] = client.get("/api/bootstrap").json()["csrf"]
    connection = next(x for x in client.get("/api/ai/connections").json()
                      if x["id"] == "local-qwen")
    response = client.post("/api/ai/requests", json={
        "connection_id": connection["id"],
        "expected_version": connection["version"],
        "request_id": uuid.uuid4().hex,
        "kind": "text",
        "text": "社区图书馆开放时间为上午九点至下午五点。",
        "allow_cloud": False
    })
    response.raise_for_status()
    job = response.json()
    while job["status"] in ("queued", "running"):
        time.sleep(1)
        response = client.get("/api/ai/requests/" + job["id"])
        response.raise_for_status()
        job = response.json()
    print(job["status"], job["result"], job["error"])
```

kind 可选 probe / text / evidence。已有图片证据指定 evidence_id；上传图片 metadata 使用 kind=evidence，不设 evidence_id。云端连接需要 allow_cloud=true。失败任务的 result 为 null。

## Linux / WSL 迁移

后端不依赖 Windows 专属模型 SDK；Windows 启动脚本与 DPAPI 密钥是本机配套部分。迁移时安装 requirements，部署 Linux 版 llama.cpp，再设置模型地址、模型 ID 和环境变量密钥。

Windows 密钥不能在 Linux 解密：勾选删除已保存密钥，填写环境变量名并保存，再重启服务加载环境变量。不应将加密数据库作为跨系统的凭据迁移方案。Linux／WSL2 及真实摄像头需在目标环境完成部署验收。

在线与离线接口共用 model_api.py 的解析及传输契约；适配入口位于 framework_part/app/services/ai_protocols.py。
