# Third-Party Notices

EmbodiScope 在以下边界内使用开源组件。项目没有复制这些项目的核心实现；依赖通过 Python 包管理器安装，并保留各自许可证。

| 项目 | 许可证 | 项目地址 | 在 EmbodiScope 中的职责 |
|---|---|---|---|
| NumPy | BSD-3-Clause | https://numpy.org/ | 数值计算、差分、互相关与鲁棒统计 |
| pandas | BSD-3-Clause | https://pandas.pydata.org/ | 表格数据处理、时序合并与 CSV 读取 |
| Apache Arrow / PyArrow | Apache-2.0 | https://arrow.apache.org/ | LeRobot Parquet 按列读取和向量数据转换 |
| Hugging Face LeRobot | Apache-2.0 | https://github.com/huggingface/lerobot | 数据字段与目录约定的兼容目标 |
| h5py / HDF5 | BSD-3-Clause / BSD-style | https://www.h5py.org/ | ManiSkill trajectory 的层级数组读取 |
| ManiSkill | Apache-2.0；部分官方资产为 CC BY-NC 4.0 | https://github.com/haosulab/ManiSkill | `PickCube-v1` 任务接口、HDF5 trajectory 字段与任务元数据约定；本项目未分发其资产 |
| SAPIEN | Apache-2.0 | https://github.com/haosulab/SAPIEN | 可选真实仿真的机器人、刚体、相机和接触力接口 |
| NVIDIA PhysX | BSD-3-Clause | https://github.com/NVIDIA-Omniverse/PhysX | 由 SAPIEN 调用的刚体动力学和碰撞求解器；项目未修改或复制求解器实现 |
| Gymnasium | MIT | https://github.com/Farama-Foundation/Gymnasium | ManiSkill 环境生命周期和动作/观测接口 |
| ImageIO / imageio-ffmpeg | BSD-2-Clause | https://github.com/imageio/imageio | 仿真 RGB 帧编码和 MP4 产物生成 |
| rosbags | Apache-2.0 | https://gitlab.com/ternaris/rosbags | ROS1/ROS2 bag 与 MCAP 读取、消息反序列化和测试数据写入 |
| MCAP Python | MIT | https://github.com/foxglove/mcap | MCAP 容器支持与 ROS2 生态互操作 |
| Rerun SDK | MIT / Apache-2.0 | https://github.com/rerun-io/rerun | 生成可在 Rerun Viewer 中打开的 `.rrd` 多模态记录 |
| Three.js 0.185.1 | MIT | https://github.com/mrdoob/three.js | 浏览器内三维轨迹、异常位置和 TCP 时间游标渲染 |
| OpenCV | Apache-2.0 | https://opencv.org/ | 可选的 ROS 图像解码、缩放和帧差计算 |
| pytest | MIT | https://pytest.org/ | 自动化测试 |
| LeRobot PushT Dataset | MIT | https://huggingface.co/datasets/lerobot/pusht | 206 条 PushT 视觉操作 Episode、Parquet 元数据与 RGB 视频；固定修订和哈希见 `SOURCE.json` |

## 方法参考，不是运行依赖

以下项目只用于任务推理与恢复协议的设计对照。EmbodiScope 没有复制其源代码，也没有把它们的规划器、行为树运行时或运动规划器打包为项目依赖：

| 项目 | 许可证 | 项目地址 | 借鉴的公开思想 | EmbodiScope 的实现边界 |
|---|---|---|---|---|
| BehaviorTree.CPP | MIT | https://github.com/BehaviorTree/BehaviorTree.CPP | 可监控技能执行、Fallback 与局部恢复 | 项目自行实现连续状态到谓词的落地、首因失效定位、恢复算子记录和 Web 事件时间轴，不运行 BT.CPP |
| py_trees | BSD-3-Clause | https://github.com/splintered-reality/py_trees | 行为状态、运行反馈和树结构可视化 | 仅作为可解释执行结构的对照，不是安装依赖，项目不运行其行为树引擎 |
| MoveIt Task Constructor | BSD-3-Clause | https://github.com/moveit/moveit_task_constructor | 分阶段操作任务和状态传播 | 项目自行实现 PickCube 任务图与受控恢复协议，不复制 MTC stage 或求解器实现 |
| PDDLStream | MIT | https://github.com/caelan/pddlstream | 符号谓词与连续约束结合 | 项目当前只输出 grounded predicate 和局部恢复计划，不调用 PDDLStream 规划器 |

## 原创实现边界

Three.js 的压缩模块和 MIT 许可证保存在 `static/vendor/`。EmbodiScope 自身实现的部分包括：统一诊断 Schema、LeRobot 数据文件筛选与特征展开、ManiSkill 字段映射、安全 ZIP 解包、ROS 话题语义映射、异步话题时间对齐、异常检测、同步偏移估计、风险约束评分、失败根因解释、保守数据修复、FaultBench、RepairBench、连续状态谓词落地、故障传播解释、RecoveryLab 配对协议、RecoveryBench 安全分离统计、Three.js 回放状态机、Rerun 实体组织、HTTP API、Web 工作台和报告生成。

开源组件负责成熟的物理仿真、文件解析、消息反序列化、视频编码和图形渲染。项目的核心主张不是重新实现这些基础设施，而是把故障事实、诊断证据、任务谓词、恢复干预和配对结果组织为可复现、可审计的具身实验闭环。
