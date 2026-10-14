# C 求解器源码

本目录是 C11 物理内核的权威源码。算法修改从这四个翻译单元开始：

| 文件 | 同步求解入口 | 职责 | 对应 Python 桥接 |
| --- | --- | --- | --- |
| [physics_native.c](physics_native.c) | `phy_simulate` | DFSPH 液体、粒子接触、沙粒与 N-body | [native_backend.py](../native_backend.py) |
| [mesh_native.c](mesh_native.c) | `mesh_simulate` | 三角网格 XPBD、布料与接触 | [mesh_solver.py](../mesh_solver.py) |
| [connections_native.c](connections_native.c) | `connections_simulate` | 点质量弹簧、杆与绳 | [connections_solver.py](../connections_solver.py) |
| [coupled_native.c](coupled_native.c) | `coupled_simulate` | 共享时钟、持久粒子/网格 context、刚体与两向接触 | [coupled_solver.py](../coupled_solver.py) |

各 `.h` 定义输入、输出、ABI 版本、状态码和缓冲区所有权；Python ctypes 结构必须与其一致。[rigid_math.h](rigid_math.h) 提供旋转刚体计算，[particle_gravity.h](particle_gravity.h) 提供粒子引力计算。持久 context 接口供耦合流程使用；单次 simulate 与 context 接口的资源寿命分别遵循头文件合同。

## 调用与编译

[backend.py](../backend.py) 根据准备计划选择后端。桥接把经过验证的场景转换为 C 数组与结构体，加载库，调用同步入口，再把已完成结果转换为 Python 轨迹和观测。Python 负责场景、初态和工件；C 负责对应数值内核的状态推进。

粒子桥接在 `native_backend.py` 中处理本机编译或明确配置的预编译库。网格、连接和耦合桥接共用 [native_runtime.py](../native_runtime.py) 的编译加载工具。耦合库链接 `coupled_native.c`、`physics_native.c` 和 `mesh_native.c`。编译缓存由源码、头文件、编译器和参数的摘要确定，缓存不作为第二份源码维护。

## 源码与发布副本

仓库中的 `toolboxes/physics/physics_release/physics_demo/core/native/` 是 [build_physics.py](../../../toolboxes/build_physics.py) 生成的交付副本，`toolboxes/physics/prebuilt/` 是构建工件。编辑本目录的权威 C/H 文件后，由构建负责人更新副本并检查摘要；已发布组合和运行中任务 pin 不就地修改。

从 demo 或公共仓库根目录检查发布副本：

```sh
python3 toolboxes/build_physics.py --check
```

场景、后端、ABI 和验证的详细要求见 [引擎架构](../../../docs/architecture.md)；全仓库目录角色见 [源码导航](../../../ARCHITECTURE.md)。
