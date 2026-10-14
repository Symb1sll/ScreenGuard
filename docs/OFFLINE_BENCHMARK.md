# 五模型离线横向评测

这里的“离线横评”指：使用同一份**冻结 test 数据集**，依次调用本地多标签模型、OpenAI、Claude、Kimi、Qwen，记录相同的标签得分并统一计算 Precision / Recall / F1 / AP / 延迟。外部模型仍通过 API 调用，因此需要网络和各平台 API Key。

默认提供商为 local、openai、claude、kimi、qwen。local 使用训练得到的多标签 `.pt` 权重；其他提供商通过 API 调用。执行前应显式配置账号实际支持图片输入的模型 ID，不能依赖示例或代码中的默认型号作为可用性保证。

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

## 先做 10 张连通性测试

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

确认 JSON 解析、网络、模型权限、标签输出都稳定后，再跑完整冻结 test 集。脚本使用 JSONL cache，满足缓存标识条件的已缓存请求可复用结果；缓存失效或配置变化时仍可能产生新调用费用。

## 横评原则

- **禁止用 test 集调阈值或改提示词**。本地模型阈值只在 val 上标定；外部模型提示词先在开发集固定后，再跑 test。
- 所有 provider 必须看到同一张原始图片，除非另行进行“预处理消融实验”。
- 至少报告 `macro_f1`、`micro_f1`、`violation_recall`、`violation_precision`、P95 延迟和错误率。
- 对企业内容安全场景，不能只按总准确率排名，应优先关注高风险类别 Recall 和漏报案例。
- API token 统计仅用于用量比较；实际货币成本应使用执行当天各平台官方价格计算，避免把过期价格写死进代码。
