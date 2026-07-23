# RecoveryLab 配对恢复实验

RecoveryLab 用真实 ManiSkill + SAPIEN + PhysX 执行回答一个比“能否生成恢复建议”更严格的问题：**在故障仍然存在的条件下，执行局部恢复是否真的恢复了任务和关键物理谓词？** Safety-aware RecoveryBench 再把该问题扩展到多个场景和随机种子，并明确禁止用“任务恢复”替代“执行安全”的结论。

## 1. 实验主张

每次实验顺序运行两个变体：

| 变体 | 故障 | 恢复干预 | 作用 |
|---|---|---|---|
| Failure | 保留 | 不执行 | 建立同一故障下的失败对照 |
| Recovered | 保留 | 首因后执行 | 验证局部恢复是否改变物理结果 |

两组固定以下受控变量：

- 相同环境：`PickCube-v1`
- 相同机器人与控制器：Franka Panda 数值雅可比闭环控制
- 相同随机种子
- 相同故障类型和注入参数
- 相同仿真步数、相机、帧率和渲染后端

因此，结果差异来自首因后的恢复动作，而不是删除故障、替换初始状态或改用更简单任务。

恢复启动由逐帧物理观测在线决定，不读取预设的恢复步骤：碰撞在接触力超过 `36 N` 后触发；夹爪失效在闭合命令与实际开度连续 4 帧失配后触发；滑脱在 `is_grasped` 从 `true` 变为 `false` 后触发。回放记录触发证据和 `predicate-violated / recovery-start` 两个独立事件，恢复动作从检测后的下一控制步开始。

## 2. 支持场景

### 2.1 Collision

首因谓词为 `collision_free`。故障组发生向下冲击后继续失败；恢复组执行：

`EmergencyStop → RetreatToSafePose → ReobserveObstacle → ReplanCollisionFreePath → ForceGuardedRetry`

验收要求任务最终成功，恢复窗口内接触力峰值不超过 `36 N`。

### 2.2 Gripper Failure

首因谓词为 `object_attached`。故障窗口内夹爪命令无法真实执行；恢复组等待执行器恢复后执行：

`HoldPosition → RestoreGripperActuation → ReplanPregrasp → CloseAndVerifyAttachment → ResumeTransport`

验收不仅检查夹爪命令，还检查实际夹爪状态、重新抓取帧和最终任务成功。

### 2.3 Grasp Slip

首因谓词为 `object_attached`。运输阶段保留 `60 N` 横向扰动，目标发生真实滑脱；恢复组执行：

`StopTransport → WaitForObjectSettlement → RelocalizeObject → ReplanPregrasp → CloseAndVerifyAttachment → ResumeTransport`

恢复策略先等待物体稳定再重定位，避免把滑脱后的旧目标位姿继续当成有效观测。

## 3. 指标

设失败组为 `F`，恢复组为 `R`：

| 指标 | 计算 |
|---|---|
| Task success delta | `int(success_R) - int(success_F)` |
| Predicate restoration | 首因后的物理信号是否重新满足 `collision_free` 或 `object_attached` |
| Recovery latency | `t(first success_R) - t(recovery-start)` |
| Post-recovery peak force | `max(force_R[recovery-start:])` |
| Path overhead | `TCP path length_R - TCP path length_F` |
| Path overhead rate | `path overhead / TCP path length_F` |
| Operator completion | 完成的恢复算子数 / 恢复算子总数 |
| Full-episode safety | Recovered 全部观测帧是否均满足 `force <= 36 N` |
| Post-intervention safety | 从在线 `recovery-start` 起是否每帧均满足 `force <= 36 N` |

TCP 路径长度由相邻三维位置的欧氏距离求和。恢复延迟使用逐帧 `success_trace`，不是用视频结束时间近似。

## 4. 质量门

本次实验只有同时满足以下六项条件才返回 `passed=true`：

1. 两组环境、种子、仿真预算、相机、初始状态和故障签名一致。
2. Failure 组产生预期失败，且 Recovered 组成功。
3. Recovered 组最终 `success=true`。
4. 首因谓词经历 `false -> true` 的物理恢复。
5. 恢复算子按计划顺序完成，并具有阶段、执行器响应、谓词或任务效果证据。
6. 谓词恢复后的接触力峰值不超过 `36 N`。

碰撞场景中的谓词恢复以恢复窗口内力峰受控和任务成功联合判断；夹爪失效与滑脱场景以恢复窗口内重新出现真实 `is_grasped=true` 且任务成功判断。

`passed` 保留为恢复质量门的兼容字段，不是整段安全结论。正式展示使用三个互不替代的 verdict：

1. `task_recovery`：受控配对、Failure 对照、最终成功、谓词恢复和算子完成是否共同成立。
2. `episode_safety`：Recovered 的完整 Episode 是否从未越过 36 N 安全不变量。
3. `post_intervention_safety`：在线恢复触发之后是否保持在安全不变量内。

因此，碰撞案例的正确结论是 `任务恢复=PASS / 整段安全=FAIL / 干预后安全=PASS`。恢复动作降低了后续风险，但不能抹去已经发生的危险碰撞。

## 5. 执行与产物

网页进入“恢复实验”，推荐使用 `seed=7 / horizon=140`。后台作业按以下顺序执行：

```text
Failure simulation
  ↓
failure/replay.json + failure/episode.mp4
  ↓
Recovered simulation
  ↓
recovered/replay.json + recovered/episode.mp4
  ↓
paired metrics + quality gates + result.json
```

产物目录：

```text
output/recovery/{job_id}/
├── failure/
│   ├── trajectory.h5
│   ├── replay.json
│   └── episode.mp4
├── recovered/
│   ├── trajectory.h5
│   ├── replay.json
│   └── episode.mp4
└── result.json
```

前端使用同一个时间游标同步两段 MP4，并从两组 `replay.json` 读取阶段、任务成功、抓取状态和接触力。证据时间线同时绘制两组力觉、`36 N` 安全线和恢复事件；点击事件标记可跳转到对应物理画面。实验结果可直接导出 JSON，服务启动时会重新索引 `output/recovery` 中经过校验的历史结果。

### RecoveryBench 批量协议

默认使用三个场景、从 `seed=7` 起准入 3 个有效种子，并设置 `horizon=140`，有效集共执行 9 组配对、18 次物理仿真。准入要求每个场景的 Failure/Recovered 都包含相同受控故障签名；若 ManiSkill 在故障注入前已报告成功，该候选 seed 会带原始帧数和排除原因进入 `excluded_seeds`，再顺延候选补足样本。批量模式关闭 MP4 录制，保留每次轨迹和 replay 作为统计证据，并通过 `SIMULATION_EXECUTION_LOCK` 串行执行 SAPIEN 环境。输出包括：

- 任务恢复率及 Wilson 95% 置信区间；
- 配对完整率与在线触发覆盖率；
- 完整 Episode 安全率与干预后安全率；
- 恢复延迟 mean/p95、路径开销 mean/p95、算子完成率；
- 逐场景统计和 seed × scenario 证据矩阵。

产物写入 `output/recovery-benchmark/{job_id}/result.json`，异步作业支持进度、取消、JSON 下载和服务重启后的历史结果恢复。

本机真实运行 `recovery-bench-20260723-002311-2c10fb` 接受 `seed=7,9,10`，排除 `seed=8`（环境在故障前第 5 帧已成功）。9 个有效配对中 8 个通过严格任务恢复判定，恢复率 `88.9%`，Wilson 95% CI `56.5%-98.0%`；完整 Episode 安全率 `66.7%`，干预后安全率、在线触发覆盖和配对完整率均为 `100%`，恢复延迟 p95 为 `3.93 s`。`collision / seed=10` 的 Failure 与 Recovered 均成功，因此虽然恢复谓词、算子和后续安全门成立，干预没有任务成功上的反事实增益，仍计为任务恢复失败。

## 6. 开源依据

- [ManiSkill](https://github.com/haosulab/ManiSkill)：确定性环境重置、机器人操作环境、物理状态与轨迹记录。
- [BehaviorTree.CPP](https://github.com/BehaviorTree/BehaviorTree.CPP)：失败检测、Fallback、局部恢复和可审计执行状态的设计思想。
- [MoveIt Task Constructor](https://github.com/moveit/moveit_task_constructor)：分阶段任务、状态传播和局部重规划的设计思想。

RecoveryLab 没有复制这些项目的任务实现。ManiSkill/SAPIEN 提供成熟的物理环境；配对实验管理、故障后策略、谓词恢复、指标、质量门、产物访问和同步对照界面由 EmbodiScope 实现。

## 7. 解释边界

RecoveryLab 验证的是一个确定性场景中的**局部干预因果效果**。它比只输出恢复计划更强，但不等价于：

- 已学习到可泛化恢复策略；
- 在未见物体、机器人或环境中仍能成功；
- 已满足真机安全认证；
- 已完成跨任务、跨环境或大规模统计显著性验证；当前 Wilson 区间只量化有限种子下的二项不确定性。

后续扩展应增加更多 ManiSkill 任务、视觉闭环重定位、动态障碍物、真机回放和恢复策略学习基线。
