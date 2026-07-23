# ManiSkill 仿真场景矩阵

EmbodiScope v2.0 将仿真从四个基础场景扩展为十个可复现实验。所有场景共享同一个 Panda `PickCube-v1` 环境、数值雅可比闭环控制器、SAPIEN RGB 相机和 PhysX 接触求解，故障只在明确的时间窗口注入。

## 场景目录

| 场景 ID | 类别 | 注入方式 | 预期结果或证据 |
|---|---|---|---|
| `nominal` | 基准任务 | 无故障 | 完成抓取并移动到目标，`success=true` |
| `collision` | 接触与安全 | 接近阶段向桌面发送向下执行器指令 | `FORCE_SPIKE` 与碰撞位置 |
| `grasp-slip` | 任务执行 | 抓取建立后对方块施加一次 60 N 横向外力 | `is_grasped: true→false`、`GRASP_SLIP` |
| `gripper-failure` | 控制与执行器 | 策略请求闭合，但物理夹爪持续保持张开 | `GRIPPER_RESPONSE_FAILURE` |
| `actuator-stall` | 控制与执行器 | 冻结控制增量 1.5 秒后恢复 | `ROBOT_STUCK`，并可观察恢复后继续完成任务 |
| `object-perturbation` | 任务执行 | 接近阶段给方块施加侧向速度 | 闭环重定位后仍可完成任务 |
| `sensor-delay` | 感知与时序 | RGB 延迟 200 ms | `SENSOR_DESYNC` |
| `frame-drop` | 感知与时序 | 连续重复上一帧并标记 6 帧无效 | `FRAME_DROP` |
| `camera-occlusion` | 感知与时序 | 抓取窗口遮挡 10 帧 RGB | 遮挡视频窗口与 `FRAME_DROP` |
| `compound-failure` | 复合压力测试 | 同时注入碰撞、200 ms 延迟与 6 帧丢失 | 一次回放分离多类故障证据 |

## 基准闭环

基准控制器不再只是“抓取尝试”。它依次执行：

1. `approach`：移动到方块上方 10 cm。
2. `reach`：下降到抓取中心附近。
3. `grasp`：闭合夹爪并验证 `is_grasped`。
4. `transport`：实时读取目标位姿并移动方块。

固定种子 7 下通常在第 65 帧左右达到 ManiSkill 的成功条件，正常接触力峰值约 28 N，不触发 35 N 的碰撞门限。

## 动作与执行反馈

`trajectory.h5` 同时记录：

- `actions`：策略产生的动作。
- `applied_actions`：实际送入物理环境的动作。
- `gripper_command`：策略夹爪意图，`-1` 为闭合、`1` 为张开。
- `gripper`：归一化实际夹爪开度。
- `is_grasped`：ManiSkill 接触求解得到的物理抓取状态。
- `phase`：`approach / reach / grasp / transport` 或故障阶段。

因此夹爪执行器失效能够表达为“策略请求闭合，但实际动作被故障层改写，夹爪反馈仍保持张开”，而不是直接伪造一条异常标签。

## 复合故障

`compound-failure` 在同一 Episode 中保留三个独立注入事件：执行器冲击、视觉延迟和连续丢帧。诊断器仍分别读取力觉、相机运动和 `frame_valid`，因此可以验证系统是否能在多故障条件下避免只输出一个笼统失败原因。
