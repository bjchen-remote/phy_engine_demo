# Architecture

The maintained architecture and dependency map is in [docs/architecture.md](docs/architecture.md).

Runtime implementations live under `physics_demo/core/`, `systems/`, `io/` and `analysis/`. Root Python modules are compatibility imports; examples contain configurations or public API calls.

## C kernel entry points


| 要修改的功能 | 权威实现 | Python 桥接 |
| --- | --- | --- |
| 液体 DFSPH、粒子、沙粒接触与 N-body | [physics_native.c](physics_demo/core/native/physics_native.c)，入口 `phy_simulate` | [native_backend.py](physics_demo/core/native_backend.py) |
| 三角网格软体、布料、XPBD 与接触 | [mesh_native.c](physics_demo/core/native/mesh_native.c)，入口 `mesh_simulate` | [mesh_solver.py](physics_demo/core/mesh_solver.py) |
| 点质量、弹簧、杆与绳 | [connections_native.c](physics_demo/core/native/connections_native.c)，入口 `connections_simulate` | [connections_solver.py](physics_demo/core/connections_solver.py) |
| 粒子、网格与旋转刚体混合场景 | [coupled_native.c](physics_demo/core/native/coupled_native.c)，入口 `coupled_simulate` | [coupled_solver.py](physics_demo/core/coupled_solver.py) |

头文件定义 C ABI 和内存所有权，桥接文件定义对应的 ctypes 布局。[C 内核导航](physics_demo/core/native/README.md) 说明编译和调用关系；算法、单位、约束和验证的详细说明保留在 [引擎架构](docs/architecture.md)。

```text
场景 → runner.py → core/backend.py 选择后端
                       → Python ctypes 桥接 → 对应 C 内核 → 轨迹和观测
                       → Python 参考实现或解析后端（由计划明确选择）
轨迹和观测 → 渲染、数据导出与质量检查
```


The maintained source is `physics_demo/`; `toolboxes/physics/physics_release/` and prebuilt binaries are generated snapshots. `agent/` and `toolboxes/pipeline/manual/` own direct-engine and stage contracts respectively; `docs/pipeline-v2/` remains a design draft. Messaging bridges are separate installations.
