# 五模型离线横向评测

离线横评使用同一份**冻结 test 数据集**，依次调用本地多标签模型、OpenAI、Claude、Kimi、Qwen，记录统一的标签得分，并计算 Precision、Recall、F1、AP 和延迟。外部模型通过 API 调用，需要网络连接和相应平台凭据。

默认提供商为 local、openai、claude、kimi、qwen。local 使用训练得到的多标签 `.pt` 权重；其他提供商通过 API 调用。执行前需配置账号可用且支持图片输入的模型 ID，并完成连通性测试。

## 环境变量

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="YOUR_VISION_MODEL_ID"

export ANTHROPIC_API_KEY="..."
export ANTHROPIC_MODEL="YOUR_VISION_MODEL_ID"

export KIMI_API_KEY="..."
export KIMI_MODEL="YOUR_VISION_MODEL_ID"
export KIMI_BASE_URL="https://api.moonshot.cn/v1"

export QWEN_API_KEY="..."
export QWEN_MODEL="YOUR_VISION_MODEL_ID"
# 生产环境推荐替换为部署地域的 Workspace 专属 OpenAI-compatible Base URL
export QWEN_BASE_URL="https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
```

## 小规模连通性测试

在工程根目录运行；以下示例假定数据位于 artifacts/datasets，训练权重位于 artifacts/training。

```bash
python src/task2_video_panel/recognition_part/benchmarking/benchmark_five_models.py \
  --manifest artifacts/datasets/real_screen/manifest.csv \
  --data-root artifacts/datasets/real_screen \
  --labels src/task2_video_panel/recognition_part/dataset/labels.yaml \
  --local-weights artifacts/training/multilabel/best.pt \
  --max-samples 10 \
  --allow-partial
```

小规模测试使用 10 张样本检查 JSON 解析、网络、模型权限和标签输出。完整评测移除 `--max-samples 10` 限制，使用全部冻结 test 集。脚本采用 JSONL 缓存；请求与缓存标识一致时复用结果，缓存失效或配置变化可能产生新调用费用。

## 横评原则

- **test 集仅用于最终评测**。本地模型阈值在 val 集标定，外部模型提示词在开发集确定；阈值与提示词固定后执行 test 集评测。
- 所有提供商使用同一张原始图片，除非另行进行“预处理消融实验”。
- 至少报告 `macro_f1`、`micro_f1`、`violation_recall`、`violation_precision`、P95 延迟和错误率。
- 企业内容安全评估同时报告各风险类别的召回率与漏报案例，避免仅以总体准确率排序。
- API token 统计用于用量比较；费用依据评测日期的提供商计费标准单独计算。
