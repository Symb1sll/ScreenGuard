# 工作台与模型工具

## 布局

- `screen_guard_core.py`：预处理契约、四点透视、画质提示、分数与逐类策略、模型包。
- `framework_part/app/services`：配置/区域版本、采集与任务、事件与审核、证据留存、模型注册、离线对照。
- `framework_part/app/routers/api.py`：工作台 API；本机会话和 CSRF 保护由 main.py 提供。
- `recognition_part/training`：模型训练、验证集阈值标定和模型包导出。
- `recognition_part/evaluation/replay_acceptance.py`：固定视频集完整回放，复用在线事件聚合。
- `services/source_profiles.py`、`auto_review.py`：每路版本配置及持久化自动辅助审核队列。
- `services/maintenance.py`、`diagnostics.py`：独立维护线程、采集和推理健康指标。
- `services/cases.py`、`routers/runtime.py`、`static/runtime-ui.js`：风险处置、设备探测和运行界面。
- `core/migrations.py`：增量数据库版本迁移；升级前锁定运行目录并备份原数据库。

日常操作见 [使用说明](使用说明.md)，目录职责见 [工程结构](工程结构.md)。

## 完整回归

在项目根目录、已安装开发依赖的 Python 环境中运行，各测试套件使用独立进程和项目内临时数据：

```bat
python -X utf8 -B tests/run_tests.py
```

入口自动生成新的临时目录，不读取工作台运行库。覆盖模型契约、权限、事件版本、自动调用、故障、配置和恢复；接口回归使用模拟提供商，不产生真实云端调用。模型一致性使用合成训练权重，不代表业务识别准确率。

## 训练示例

从工程根目录运行以下命令；`python` 应指向已安装训练依赖的解释器。

```text
python -B src/task2_video_panel/recognition_part/dataset_tools/make_mock_multilabel_dataset.py --out artifacts/training/mock_multilabel
python -B src/task2_video_panel/recognition_part/training/train_multilabel_classifier.py --manifest artifacts/training/mock_multilabel/manifest.csv --data-root artifacts/training/mock_multilabel --labels src/task2_video_panel/recognition_part/dataset/labels.yaml --arch mobilenet_v3_small --epochs 1 --workers 0 --batch-size 8 --image-size 96 --out artifacts/training/new_letterbox_smoke
```

此命令使用合成素材，不下载预训练权重。正式训练须替换真实、已审核且合理分组拆分的素材；清单只接收明确标注 status=approved 的样本。新权重保存 letterbox_rgb_v1，旧权重无版本时保留 legacy_square_rgb_v0。

## 双阈值候选

先用 evaluate_multilabel_classifier.py 的 `--split val` 输出 predictions.csv 及 metrics.json，再运行：

```text
python -B src/task2_video_panel/recognition_part/training/calibrate_review_policy.py --predictions artifacts/training/validation/predictions.csv --out artifacts/training/review_policy.json --data-kind real --min-precision 0.98 --review-recall 0.9 --min-support 20
```

阈值标定仅使用 val 集。各类候选需满足正负样本数与高阈值命中支持数要求；生成报告的 `approved_for_alerts` 固定为 `false`。候选策略须经过独立测试和业务审核，图像级指标与视频时序事件指标分别评估。

## 模型包

```text
python -B src/task2_video_panel/recognition_part/training/export_model_package.py --weights artifacts/training/new_letterbox_smoke/best.pt --out artifacts/training/packages/model-v1 --model-id model-v1
```

可附加 `--policy-report artifacts/training/review_policy.json`。输出目录必须不存在；包含 model.onnx 和 manifest.json，随后在“素材与模型”中导入这两个文件。

导入验证模型哈希、标签顺序、尺寸、预处理与 logits 输出，执行 ONNX 预热；不接受外部权重文件。导出模型的状态为 `candidate`，生产告警需满足下述准入条件。

自动告警必须同时满足：模型声明已业务验证、验证报告 approved_for_alerts=true 且 data_kind=real、每个启用类别 eligible=true、当前逐类配置与该包验证配置一致、用户开启自动告警、同类高分连续确认与冷却规则。验证报告由本地操作者维护，不含数字签名；准入状态须依据实际测试与审核结果填写。

模型或阈值切换前，在页面运行对应候选对照，再带入设置草稿并保存；采集任务下次启动时使用新配置。回滚同样保留历史和对照记录。

## 完整视频回放验收

页面首帧对照是快速检查。完整回放接受冻结 test 清单，保留 SHA-256、标注、配置和模型版本。例如：

```json
{
  "split": "test",
  "data_kind": "real",
  "clips": [{
    "id": "camera1_clip001",
    "path": "camera1_clip001.mp4",
    "sha256": "填入视频的实际 SHA-256",
    "exhaustively_annotated": true,
    "points": [[0,0],[1,0],[1,1],[0,1]],
    "truth": [{"start": 2.0, "end": 5.0, "labels": ["gambling"]}]
  }]
}
```

truth 的时间单位为视频秒，可含多标签、多个区间。只有逐段完整审核、确认遗漏区间为正常，才能设置 exhaustively_annotated=true。清单中各视频应代表不重叠的摄像头时间。只标了部分风险片段时设置 false，工具不会报告完整误报率。

```text
python -B src/task2_video_panel/recognition_part/evaluation/replay_acceptance.py --manifest replay/test.json --baseline artifacts/training/packages/model-v1 --candidate artifacts/training/packages/model-v2 --baseline-settings replay/baseline.json --candidate-settings replay/candidate.json --out artifacts/training/replay/run-new
```

模型参数也接受 demo-rules。settings 文件为工作台 Settings 对象或含 settings 字段的 API 响应；不提供则使用默认策略。输出目录必须不存在。输出 report.json 与独立运行库/证据，不修改工作台配置。

指标按类别以时间区间 IoU≥0.3 一对一匹配。事件召回覆盖 review/alert；告警精确率仅评价实际通过准入和连续确认的 alert。未标注、无真实告警或缺少分母时，相关指标输出 `null`。小时／天维度的指标由回放结果折算，用于估计审核负担。解码与推理失败数、处理耗时单独报告；画质不足的样本标记为未知并保留。

低分随机抽检的种子由视频哈希固定，用于可重复对照。用户误报备注和真实审核耗时在工作台留档；导出的人工反馈仍标为 candidate，不能直接自动训练。

## 运行和留存边界

视频采样采用固定时间间隔，在线 ONNX 推理使用 CPU；变化触发、OCR 和硬件解码属于规划功能。训练支持兼容的 GPU 环境。每路可配置自动本地辅助审核及授权云端补充，云端调用按提供商账户计费。AI 建议独立于人工结论和自动告警准入。

证据配额不包含上传视频、模型、数据库和日志；已确认风险证据保留。期满疑似证据会清理，事件/审核摘要保留。日复核时间划分待审批次并显示下次安排。工作台提供管理员／审核员账号、通知签收和风险处置。平台与设备测试范围见 [README](../README.md#linux--wsl2-迁移)。
