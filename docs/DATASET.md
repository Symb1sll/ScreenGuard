# 真实公共大屏数据集建设规范

数据格式示例位于 `src/task2_video_panel/recognition_part/dataset`。训练、验证、测试使用 CSV manifest 和多标签向量。

## 1. 推荐采集规模

初期可将 2,000–5,000 张经人工确认的真实大屏截图作为数据建设起点，并优先补充正样本不足 200 张的风险类别。这些数量用于规划采集工作，不代表准确率达标条件；最终规模应由场景覆盖、类别难度和独立测试结果决定。生产评估使用完全冻结、来源隔离的独立测试集。

## 2. 标签

风险标签见 `src/task2_video_panel/recognition_part/dataset/labels.yaml`：

- `sexual_vulgar`
- `gambling`
- `drugs_contraband`
- `violence_terror`
- `privacy_leak`

一张图可同时拥有多个标签。`labels` 为空表示正常样本，不额外设置 `normal=1`，避免“正常+违规”矛盾标签。

## 3. Manifest 字段

`image_path` 为相对 manifest 所在目录或通过脚本指定的数据根目录；`labels` 用 `|` 分隔；`group_id` 必须代表同一视频源的一段连续时间窗口，例如 `screen_001_20260901_0800_0830`。同一 `group_id` 必须只出现在 train/val/test 其中一个集合中。

`status=approved` 才进入训练与正式评测；`uncertain` 进入人工复核池；`rejected` 表示图片损坏、与任务无关或不应进入数据集。

## 4. 为什么必须按 group 切分

公共大屏视频相邻帧高度相似。如果直接随机按图片切分，几乎相同的连续帧可能同时进入训练和测试，造成指标虚高。项目中的 `split_manifest.py` 按 `group_id` 做稳定哈希切分，从源头防止这种泄漏。

## 5. 标注规则

建议双人标注 + 冲突复核：

1. 第一位标注员独立标注；
2. 第二位标注员对全部风险样本和至少 20% 正常样本复核；
3. 冲突样本标记 `uncertain`，由高级审核员裁决后改为 `approved`；
4. 记录 `reviewer` 和 `notes`，便于追踪错误类型。

不要把“看起来可能有风险”直接标为正类。只有画面证据足够时才标正类；证据不足应进入 `uncertain`。

## 6. 数据验收

在工程根目录、训练前运行（示例假定真实数据保存在 artifacts/datasets）：

```bash
python src/task2_video_panel/recognition_part/dataset_tools/validate_manifest.py \
  --manifest artifacts/datasets/real_screen/manifest.csv \
  --data-root artifacts/datasets/real_screen \
  --labels src/task2_video_panel/recognition_part/dataset/labels.yaml \
  --report artifacts/evaluation/dataset_validation.json
```

验收重点：图片是否可读取、SHA256 完全重复、近重复、标签合法性、各 split 样本量、每类正样本数、group 跨 split 泄漏。

## 7. 真实数据安全

公共大屏截图可能包含人脸、车牌、手机号、身份证等敏感信息。建议采集端只保存完成任务所需的最小数据，设置保留周期、访问控制和操作审计；对用于研发共享的数据集进行脱敏并遵守所在地适用法律、合同与平台政策。
