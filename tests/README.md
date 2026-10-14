# 验证入口

所有命令从工程根目录执行，Python 3.11。输出集中到 `artifacts/`，每次使用独立目录；自动套件不读取正式运行库，也不调用真实云端。

- `python -X utf8 -B tests/run_tests.py`：完整回归，各套件使用独立进程，包含布局和路径、核心、运行调度、模型协议、识别工具、企业权限及进程守护。
- `python -X utf8 -B tests/check_windows_launchers.py`：Windows 根目录 BAT 入口实测，使用独立数据目录和端口，从其他工作目录启动、重复启动并停止。
- `python -X utf8 -B tests/simulate_cameras.py --seconds 60`：本机合成 HTTP 视频流，验证一至四路、断流恢复、重启及停止。
- `python -X utf8 -B tests/ui_fixture.py`：独立页面和模拟模型接口，启动地址与状态写入 `artifacts/tests/browser_state.json`。
- `python -X utf8 -B tests/local_acceptance.py`：手动启动的真实本地 Qwen 自动调用验收，会加载外部 GGUF/mmproj。
- `python -X utf8 -B tests/warm_model_cameras.py`：手动启动的真实 Qwen 与四路并行验收，会使用大量内存。

后两项不包含在默认回归中；文件路径从本机 `config/runtime_config.json` 读取，环境变量 `SCREEN_GUARD_LOCAL_MODEL`、`SCREEN_GUARD_LLAMA_SERVER`、`SCREEN_GUARD_MMPROJ` 优先。缺少配置或文件时停止，不下载模型。业务准确率、真实摄像头及 Linux 长时间运行需要另外验收。
