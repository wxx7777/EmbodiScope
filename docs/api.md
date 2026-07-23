# EmbodiScope API 与适配器接口

## HTTP API

服务默认监听 `http://127.0.0.1:8765`。

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/health` | 服务、数据集、当前适配器与 Rerun 可用状态 |
| `GET` | `/api/adapters` | 可用开源适配器、格式、依赖与许可证 |
| `GET` | `/api/datasets` | 本地白名单数据集目录、来源、许可证、模态和规模 |
| `GET` | `/api/profiles` | 可用诊断 Profile、阈值、物理下限与当前配置 |
| `GET` | `/api/dataset` | 数据集概览、Episode 列表和来源元数据 |
| `GET` | `/api/audit` | 跨 Episode 等级、故障、维度、排名和来源 SHA-256 |
| `GET` | `/api/audit.json` | 下载完整数据集审计 JSON |
| `GET` | `/api/audit.csv` | 下载扁平 Episode 审计 CSV |
| `GET` | `/api/embodied` | observation/action/next-state/contact/outcome 策略数据就绪度 |
| `GET` | `/api/embodied.json` | 下载具身策略数据契约评估 JSON |
| `GET` | `/api/task-reasoning` | 连续谓词、技能任务图、首因与恢复算子 |
| `GET` | `/api/task-reasoning.json` | 下载完整任务推理 JSON |
| `GET` | `/api/dataset/video/{episode_id}` | 当前数据集对应 Episode 的 Range MP4 视频流 |
| `GET` | `/api/episode/{id}` | 单个 Episode 的评分、问题、根因和时序信号 |
| `GET` | `/api/repair/{id}` | 生成或读取单个 Episode 的可追溯清洗预览 |
| `GET` | `/api/repair/download/{id}` | 下载带质量掩码、原值备份和分段标记的清洗 CSV |
| `GET` | `/api/repair/manifest/{id}` | 下载清洗动作、Profile、问题变化与双 SHA-256 manifest |
| `GET` | `/api/batch-repair/status` | 最近批量清洗作业与当前活动作业 |
| `GET` | `/api/batch-repair/status/{job_id}` | 单个批量清洗作业的进度、结果或错误 |
| `GET` | `/api/batch-repair/download/{job_id}/{artifact}` | 下载 `package`、`parquet`、`manifest` 或 `summary` 产物 |
| `GET` | `/api/report/{id}` | 下载 Markdown 诊断报告 |
| `GET` | `/api/rerun/{id}` | 生成并下载包含轨迹、标量与事件的 Rerun `.rrd` 记录 |
| `GET` | `/api/simulation/catalog` | 仿真运行时、环境、控制器和故障场景目录 |
| `GET` | `/api/simulation/status` | 最近仿真作业与当前活动作业 |
| `GET` | `/api/simulation/status/{job_id}` | 单个作业的状态、进度、配置和结果 |
| `GET` | `/api/simulation/replay/{job_id}` | 获取同步回放 JSON |
| `GET` | `/api/simulation/video/{job_id}` | 支持 HTTP Range 的 H.264 MP4 流 |
| `GET` | `/api/recovery/catalog` | RecoveryLab 协议、支持场景、谓词、运行时与开源依据 |
| `GET` | `/api/recovery/status` | 最近恢复实验与当前活动作业 |
| `GET` | `/api/recovery/status/{job_id}` | 单个配对恢复实验的状态、进度和结果 |
| `GET` | `/api/recovery/replay/{job_id}/{variant}` | 获取 `failure` 或 `recovered` 的同步回放 JSON |
| `GET` | `/api/recovery/video/{job_id}/{variant}` | 获取 `failure` 或 `recovered` 的 Range MP4 |
| `GET` | `/api/recovery/result/{job_id}` | 下载配对实验结果 JSON |
| `GET` | `/api/recovery-benchmark/status` | 最近 RecoveryBench 作业与当前活动作业 |
| `GET` | `/api/recovery-benchmark/status/{job_id}` | 多场景多种子批测的进度、配置和结果 |
| `GET` | `/api/recovery-benchmark/result/{job_id}` | 下载 RecoveryBench 统计结果 JSON |
| `POST` | `/api/upload` | 上传 Base64 编码的 CSV、Parquet、LeRobot ZIP、HDF5、MCAP、bag 或 db3 文件 |
| `POST` | `/api/reset` | 恢复内置 CSV 演示数据 |
| `POST` | `/api/datasets/load` | 通过目录 ID 安全切换本地数据集 |
| `POST` | `/api/profile/load` | 切换当前诊断 Profile，并重新分析数据集 |
| `POST` | `/api/benchmark/run` | 运行或读取缓存的 FaultBench 统计评测 |
| `POST` | `/api/repair-benchmark/run` | 运行或读取缓存的 RepairBench 清洗效果评测 |
| `POST` | `/api/batch-repair/run` | 对当前数据集全部 Episode 提交后台清洗与训练数据打包作业 |
| `POST` | `/api/batch-repair/cancel` | 按 `job_id` 取消排队或运行中的批量清洗作业 |
| `POST` | `/api/simulation/run` | 提交后台 ManiSkill 仿真作业 |
| `POST` | `/api/simulation/cancel` | 取消排队或运行中的作业 |
| `POST` | `/api/simulation/load` | 将已完成轨迹一键载入诊断工作台 |
| `POST` | `/api/recovery/run` | 提交 RecoveryLab 配对故障/恢复实验 |
| `POST` | `/api/recovery/cancel` | 按 `job_id` 取消恢复实验 |
| `POST` | `/api/recovery-benchmark/run` | 提交多场景多种子 RecoveryBench |
| `POST` | `/api/recovery-benchmark/cancel` | 按 `job_id` 取消 RecoveryBench |

上传请求：

```json
{
  "filename": "episode.mcap",
  "content_base64": "..."
}
```

文件上限为 25 MB。LeRobot ZIP 解压后限制为 150 MB 和 2000 个文件，并检查路径穿越。大型 ROS2 bag 或 LeRobot 数据集应使用 CLI。

切换到 Hugging Face PushT：

```json
{"dataset_id": "lerobot-pusht"}
```

目录只接受 `data/dataset_catalog.json` 中的 ID，不接受任意文件路径。单个 Episode 响应中的 `media` 字段包含 `available / url / start / end / duration / feature`；前端用局部时间加 `start` 定位合并视频中的真实片段。

切换诊断 Profile：

```json
{"profile_id": "franka-panda"}
```

运行 FaultBench：

```json
{"seed_count": 8}
```

`seed_count` 范围为 2-25。响应包含 `protocol`、`metrics`、`baseline`、`comparison`、`performance` 和 21 项故障强度矩阵。相同 Profile 与种子数量在进程内缓存；页面导出的 JSON 保留全部阈值和运行环境信息。

运行 RepairBench：

```json
{"seed_count": 4}
```

`seed_count` 范围为 2-12。响应包含 `protocol / metrics / performance / quality_gates / per_class / matrix`。协议覆盖短缺口插值、孤立关节突跳、视觉偏移校正、时间缺口分段、接触力风险隔离和正常轨迹保护。4 个种子形成 64 条轨迹；当前正式结果为修复成功率 100%、重建 RMSE 0.00013753、同步残差 0 ms、正常过度修复率与误隔离率 0%，6/6 质量门通过。

提交整套数据集批量清洗无需请求参数，服务使用当前数据集和当前 Profile，成功时返回 HTTP `202`：

```json
{
  "id": "clean-20260719-210411-ecf489",
  "status": "queued",
  "episode_count": 6,
  "row_count": 3600
}
```

轮询 `/api/batch-repair/status/{job_id}`；完成后 `result.summary` 给出输入、修改、隔离、保留行数与保留率。下载标识映射为：`package` -> ZIP、`parquet` -> `cleaned.parquet`、`manifest` -> `manifest.json`、`summary` -> `episode_summary.csv`。所有结果与下载响应使用 `Cache-Control: no-store`。

数据集审计响应在每条 Episode 上增加 `issue_codes / scores / missing_rate / primary_issue_code`，并聚合 `grade_distribution / score_histogram / issue_code_counts / average_dimension_scores / worst_episodes / training_ready_episodes`。`/api/audit.csv` 将五维评分展开为独立列，并在每一行附带同一个来源 SHA-256。

任务推理响应包含 `operators / predicate_labels / episodes / failure_predicates / protocol`。每条 Episode 的 `trace` 按技能列出前置条件、效果、`true / false / unknown`、物理证据和置信度；`first_violation` 给出最早失效谓词、技能与时间，`causal_chain` 解释失败传播，`recovery_plan` 按安全、控制、感知、规划和验证生成局部恢复算子。该 API 不声明恢复已执行或成功。

清洗接口采用保守策略：短缺口和孤立突跳可以校正，多模态偏移在置信度达标时校正；碰撞、卡滞、滑脱、工作空间越界和无效视觉帧只设置 `quality_valid=false`，不改写真实测量。时间缺口只建立 `segment_id`，不会生成不存在的采样点。CSV 中被修改的信号保留 `{column}__original`，并增加：

| 字段 | 含义 |
|---|---|
| `source_row` | 当前数据集中的原始行号 |
| `quality_valid` | 是否通过训练数据质量门 |
| `repair_actions` | 本行执行的校正、分段或隔离代码 |
| `repair_reason` | 触发质量决策的原因代码 |
| `segment_id` | 按异常时间间隔切分的连续片段编号 |

manifest 的 `provenance.source_sha256` 对源文件或整个数据集目录确定性计算，`artifact_sha256` 对实际下载 CSV 字节计算。`issue_resolution` 明确列出修复前、已校正、已解决、未解决和已隔离的问题，避免把物理风险误报为“已修复”。

批量清洗将每条 Episode 的单条清洗结果合并，`cleaned.parquet` 保留原始信号、`source_row / quality_valid / repair_actions / repair_reason / segment_id` 和必要的 `__original` 列；`episode_summary.csv` 提供逐 Episode 前后评分、保留率和问题变化；`manifest.json` 记录完整 Profile、策略、来源哈希和三个内部产物哈希；ZIP 只包含上述三个文件。

提交仿真作业：

```json
{
  "env_id": "PickCube-v1",
  "scenario": "collision",
  "seed": 7,
  "steps": 40,
  "fps": 20,
  "width": 320,
  "height": 240,
  "record_video": true
}
```

`scenario` 支持 `nominal`、`collision`、`grasp-slip`、`gripper-failure`、`actuator-stall`、`object-perturbation`、`sensor-delay`、`frame-drop`、`camera-occlusion` 和 `compound-failure`。目录响应同时返回 `category / category_name / expected / recommended_steps`，前端按任务、控制、接触、感知和复合压力测试分组。服务同一时间只运行一个仿真作业，避免多个 SAPIEN 渲染上下文竞争；作业产物仅允许通过经过校验的作业编号和白名单文件名访问。

提交 RecoveryLab 配对实验：

```json
{
  "scenario": "grasp-slip",
  "seed": 7,
  "horizon": 140
}
```

`scenario` 当前支持 `collision / gripper-failure / grasp-slip`，`horizon` 必须在 100-160。实验不会用无故障 `nominal` 冒充恢复组：Failure 与 Recovered 都保留同一故障，后者只在 `recovery-start` 后增加局部恢复动作。完成结果包含 `failure / recovered / verdicts / metrics / quality_gates / plan / comparison / variants`。`verdicts` 独立报告 `task_recovery / episode_safety / post_intervention_safety`；`variants` 给出两组视频与回放地址；`metrics` 报告成功差值、谓词恢复、恢复延迟、路径开销、恢复后力峰值和算子完成率。

恢复质量门检查六项条件：配对配置/初始状态/故障签名一致、Failure 产生预期失败、Recovered 任务成功、首因谓词恢复、恢复算子按顺序完成、谓词恢复后的接触力不超过 `36 N`。`result.passed=true` 是兼容字段，只代表恢复质量门通过；安全结论必须读取独立 `verdicts`，不能由该字段推断。

提交 RecoveryBench：

```json
{
  "seed_count": 3,
  "base_seed": 7,
  "horizon": 140,
  "scenarios": ["collision", "gripper-failure", "grasp-slip"]
}
```

`seed_count` 允许 2-8。系统从 `base_seed` 起扫描候选；只有配对配置、初态和受控故障签名完整的 seed 才进入统计，故障注入前环境已终止的候选写入 `protocol.excluded_seeds` 并由后续 seed 补足。每个准入种子/场景顺序运行 Failure 与 Recovered，批量模式不录制 MP4；所有物理执行通过全局仿真锁串行化。结果中的 `summary`、`per_scenario` 和 `matrix` 分别给出总体统计、逐场景剖面和 seed × scenario 证据。任务恢复率同时报告 Wilson 95% 置信区间；完整 Episode 安全与在线干预后的安全率始终分开。

## 统一数据 Schema

适配器至少输出：

| 字段 | 类型 | 含义 |
|---|---|---|
| `timestamp` | float | Episode 内相对时间，单位为秒 |
| `episode_id` | string | 轨迹或实验标识 |

可选标准字段包括 `joint_*`、`action_*`、`ee_x/y/z`、`camera_motion`、`frame_valid`、`force_z`、`gripper`、`object_distance`、`phase` 和 `success`。分析器与三维工作台根据实际存在的信号渐进启用能力。

`/api/dataset` 与 `/api/episode/{id}` 还返回 `analysis_profile`，包括关节速度 MAD 系数、物理速度下限、卡滞时长、同步阈值、力阈值和 XYZ 工作空间。关节速度统一按 `rad/s` 计算，不受采样频率变化影响。

## 适配器契约

所有适配器实现 `DatasetAdapter` 协议：

```python
class DatasetAdapter(Protocol):
    info: AdapterInfo

    def can_load(self, path: Path) -> bool: ...
    def load(self, path: Path) -> LoadedDataset: ...
```

`LoadedDataset` 包含：

- `frame`：标准化 pandas DataFrame
- `source_format`：面向用户的数据格式名称
- `adapter_id / adapter_name`：适配器身份
- `metadata`：原始列、ROS 话题、关节名称、MCAP 消息统计等
- `warnings`：缺失时间戳、缺少 Pose 等非致命导入提示

新增格式时，在 `embodiscope/adapters/` 中实现协议，并在 `registry.py` 注册。检测算法、Web API 和 CLI 无需修改。

## LeRobot 与 ManiSkill 映射

LeRobot 目录适配器优先读取 `data/` 下的 episode Parquet，避免将 `meta/episodes/*.parquet` 当成时序样本；同时读取 `meta/info.json`、`meta/tasks.parquet`、Episode 视频偏移、数据集版本与来源清单。二维 PushT 状态映射为 `state_*`，不会被误当成弧度关节并触发假告警。

| 来源字段 | 标准字段 |
|---|---|
| LeRobot `episode_index` | `episode_id` |
| LeRobot `observation.state` | `joint_*` |
| LeRobot `action` | `action_*` |
| LeRobot `observation.ee_pose` | `ee_x / ee_y / ee_z` |
| ManiSkill `obs/agent/qpos` | `joint_*` |
| ManiSkill `actions` | `action_*` |
| ManiSkill `obs/extra/tcp_pose` | `ee_x / ee_y / ee_z` |
| ManiSkill `obs/extra/force` | `force_z` |
| ManiSkill `obs/extra/camera_motion` | `camera_motion` |
| ManiSkill `obs/extra/frame_valid` | `frame_valid` |
| ManiSkill `goal_pos - tcp_pose` | `object_distance` |

## 仿真回放契约

`replay.json` 包含统一长度的 `timestamps`、`links`、`tcp`、`object`、`goal`、`force`、`action_norm`、`frame_valid`、`phases`、`is_grasped` 和 `success_trace`。`recovery` 保存在线监视器的触发类型、失效证据、触发步、谓词恢复步和成功步；`events` 同时包含故障注入、`predicate-violated / recovery-start / predicate-restored / recovery-success` 与诊断事件，并通过 `source` 区分。RecoveryLab 使用逐帧 `success_trace` 计算恢复延迟，并用事件和力觉曲线驱动浏览器证据时间线。

## Rerun 导出

`/api/rerun/{id}` 使用 `rerun-sdk` 创建独立 recording，写入：

- `world/end_effector/trajectory`：完整 TCP 三维轨迹
- `world/end_effector/current`：随时间移动的 TCP 点
- `signals/*`：力觉、夹爪、目标距离和关节标量
- `diagnostics/events`：严重或警告事件的时间标注
- `diagnostics/summary`：质量分和问题数量

## ROS 话题映射

| 消息类型/话题 | 标准字段 |
|---|---|
| `sensor_msgs/msg/JointState` | `joint_1 ... joint_n` |
| `PoseStamped`、`Odometry`、`TransformStamped` | `ee_x / ee_y / ee_z` |
| `WrenchStamped` | `force_x / force_y / force_z` |
| `Image`、`CompressedImage` | 帧差得到 `camera_motion` |
| 包含 `gripper` 的标量话题 | `gripper` |
| 包含 `object_distance` 的标量话题 | `object_distance` |
| 包含 `success` 的 Bool 话题 | `success` |

异步话题以 JointState 为首选基准流，使用 `merge_asof` 和各流自适应容差完成时间对齐。
