# 模拟模块内部架构

模拟模块消费已验证的 `pipeline-model/1`，执行固定引擎一次，并发布独立于视频的 `pipeline-simulation/1` 数值结果。用户工具仍是 `physics_simulate` / `physics_inspect` / `physics_query` 或 PCB 对应接口；内部 `simulation.run` 不是 QQ 工具。操作说明见 [Skill](SKILL.md)，组合状态与 pin 见 [组合架构](../../ARCHITECTURE.md)。

```text
模型 bundle + 固定预算
  → schema / model SHA-256 / engine identity / ready / limits 校验
  → 新的专用输出目录
  → physics runner.simulate(make_video=False) + runner.inspect
      或 pinned pcb_thermal.simulate_pcb_thermal
  → delivery / numerical quality gate
  → 原始 JSON、标准模型、可用数值 CSV
  → simulation-manifest.json → 组合封存 checkpoint
```

## 实现分层

| 实现 | 职责 |
|---|---|
| `pipeline/worker.py` | 固定参数 subprocess 入口，读取 stage role 和请求，调用 `run_simulation` |
| `pipeline_stages/simulation.py` | 输入身份与执行限制、领域分发、质量门合并、数值 CSV 和 stage manifest |
| `pipeline_stages/common.py` | 严格 JSON、canonical/hash、不可覆盖工件、新输出目录、引擎 pin、预算检查 |
| 固定引擎 `physics_demo.runner` | 规范化模型、数值规划、状态推进、记录和持久结果验收；包含实际动力学 |
| 固定引擎 `pcb_thermal` | 厚度平均板模型的有限体积热求解与能量验收 |
| 组合 `adapter.py` | 调用顺序、剩余总预算、checkpoint、恢复和最终交付；模拟模块不发 QQ |

模拟模块不维护第二套物理方程，不因模板/关键词另写求解循环。mechanical 引擎的后端路由、ABI、保真声明与测量合同继续以根 `docs/architecture.md` 和引擎能力发现为权威。

## 输入必须与准备一致

`run_simulation` 在创建数值输出目录前校验：

- `schema_version=pipeline-model/1`，model 为字典且 canonical 摘要匹配；
- `source_engine` 等于进程中固定的引擎身份；
- modeling quality 是当前支持的 standard，preparation.ready_to_simulate 为 true；
- execution_limits 与 host 的 budget_seconds/unlimited 完全相同；
- domain 只允许 physics 或 pcb_thermal。

改变时间预算、物理窗口、queries 或材料参数均需重新 prepare。`budget_seconds=None` 的无固定时限语义保留；`unlimited` 是可信宿主字段，不能由消息覆盖。NaN、Infinity、重复 JSON 键、软链接和非专用输出目录被拒绝。

## Mechanical 路径与质量门

引擎 `runner.simulate` 使用 `make_video=False`，数值工作不会被渲染失败阻断。`result.json` 保持原 query 位置；在引擎完成专用目录文件清单检查之后才加入 stage 元数据。模拟模块随后用 `runner.inspect` 重新验证持久结果，而不是仅信任第一次返回的布尔值。

`succeeded` 同时要求运行 summary 与 inspect 的 ok / quality_gate.passed。`numerical_passed` 同时取两份结果的 numerical 门；`data_usable` 只有 succeeded 且 numerical_passed 才为 true。visual 预览可以通过交付门并携带精度 warnings，仍不支持定量结论。模块不会把呈现质量或有一个 result 文件当作数值通过。

mechanical 数据分两类：

| 数据 | 来源与用途 |
|---|---|
| solver observations / measurements | 预声明 queries，t=0 与完成宏步的 float64 归约；支持原 query 与通过质量门的 CSV |
| trajectory display frames | 求解器为展示保存的采样/可能舍入状态；粒子可能抽样，不能恢复全量历史状态、任意新观测或重启 |

只有 data_usable 且 measurements 已保存时生成 `measurements.csv`。标识符是外部数据，写 CSV 时保护开头的公式字符；数字保持原符号和数值。新数值需求不能从显示帧补造，必须在新模型中预声明并重算。

## PCB 路径与物理假设

PCB 求解保存 `result.json`、summary、终态网格和时间序列。数值质量来自明确通过的 energy balance，有限墙钟预算还要求运行时未越界。成功且可定量使用时输出温度场 CSV 与温度极值序列 CSV。

场坐标是 cell center，米制；row 从板底边开始；温度为摄氏度，power_w 为各网格单元瓦数。温度 snapshots 和标量时间序列具有各自采样时钟，均不是任意时刻的完整重启状态。

厚度平均二维模型、预设功率、对流边界和有效系数是物理假设。有限值与能量平衡约束数值一致性，不证明板材标定、真实封装热点、误差上界或工程认证。渲染/导出层还会重算持久 PCB gate 及模型绑定。

## 工件与不可变性

输出目录必须新建或为空；stage JSON 用 absent-only 的原子写入发布，拒绝覆盖已有运行。结果清单为：

```text
result.json / 引擎原始场景、规划、测量、summary、completion（实际存在的文件）
model.json                       # 原模型 bundle 副本
measurements.csv 或 PCB CSV       # 仅质量允许的数值数据
simulation-manifest.json          # pipeline-simulation/1
```

manifest 绑定原引擎身份、model bundle 文件摘要、model canonical 摘要、result 引用、所有 artifacts 的 path/hash/bytes、质量门、data_usable、采样能力与 physics_claims。组合对 manifest 和全部声明工件封存 checkpoint；后续渲染复用时再重算哈希。

失败前已写出的证据保留，不能删除目录强行重复运行。成功模拟可在 ZIP / 渲染失败后继续使用；不完整且未封存的非空输出目录需要明确恢复，不能当作缓存成功，也不能盲目覆盖。

## 扩展与回归

扩展新物理首先进入固定引擎及独立数值验收，再暴露到标准化 model 和 capabilities；不能只修改 CSV 或 Skill 声称新增求解能力。新的数据字段必须说明单位、时钟、抽样、质量和物理近似。

`toolboxes/tests/test_pipeline_stages.py` 覆盖真实无视频求解、宏步 CSV、PCB 数据、模型篡改、引擎错配、预算漂移、不可覆盖、unsupported high、visual 数值边界与 unlimited 合同。根 `tests/` 验证实际粒子、网格、连接、耦合、摆、查询和求解器；组合测试另验证恢复不会重复积分。
