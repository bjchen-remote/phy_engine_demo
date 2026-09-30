# 三阶段组合内部架构

`physics-pipeline` 0.2.0 是 QQ toolbox v1 的组合适配器。宿主仍使用 `qq_toolbox`、原 `physics_*` / `pcb_*` 工具、`physics_simulate` 与 `qq_video`；模块分拆不改变消息权限、held 交付或物理引擎接口。入口、包格式和构建命令见 [README](README.md)，客户端边界见 [整体架构](../../docs/current-architecture.md)。

```text
宿主 inbox pin → task.json → adapter.main
   → verify_bundle + 当前任务 bundle-lock + execution.lock
   → api：参数/几何/图片资产 → prepare → 冻结 plan
   → run：校验 plan
       → modeling worker → 模型 bundle → 封存 checkpoint
       → simulation worker → 数值结果/测量/质量门 → 封存 checkpoint
       → rendering worker.export → 完整 data.zip
       → rendering worker.render → 解码验证 simulation.mp4
       → 再核验 simulation checkpoint → v1 result-manifest
   → 宿主二次核验 → 原子解除 held → 视频/ZIP 独立回执
```

## 文件与职责

| 文件 | 职责 |
|---|---|
| `adapter.py` | v1 API 兼容、质量设置、准备摘要、串行阶段调用、checkpoint、恢复及最终交付清单 |
| `bundle.py` | 严格 JSON、路径隔离、目录摘要、stage 格式和完整组合验证 |
| `worker.py` | 固定 argv 单阶段进程入口；按 module role 调用相应 Python 实现，失败写结构化响应 |
| `image_modeling.py` | 可选 Mac 本地图片建模：当前事件图片 ID、可信运行时 pin、生成回执和网格资产 |
| `../build_pipeline.py` | 生成独立 stage 包，复制固定引擎与组件，写组合锁和 v1 toolbox manifest |
| `../pipeline_stages/` | 建模、模拟和不可覆盖的模型/数据工件辅助函数 |
| `../pipeline_presentation/` | 独立读取保存结果、完整 ZIP 导出、科学渲染和呈现回执 |
| `manual/<role>/` | 每个版本随模块维护的 Skill 和内部架构说明 |

QQ 宿主不导入这些领域实现；适配器与 worker 可以导入被组合固定的引擎。阶段进程不是客户端工具，也不能给 QQ 发消息。传输、权限、模型提供方配置、令牌和聊天记录不属于公开组件包。

## 三层 pin 与信任来源

第一层是宿主 `toolbox-pin.json`：registry、组合 digest、id、version 在消息入队时保存。第二层是包内 `bundle-lock.json`：固定引擎和每个 stage 的 id、version、相对目录与内容摘要。第三层是当前任务 `work/pipeline/bundle-lock.json`：首次调用后拒绝任何组件组合变化。

`bundle.verify_bundle` 对每份组件重新验证格式、入口、路径、network=false 与摘要；stage 的 input/output 格式必须精确匹配：

| role | input_schema | output_schema |
|---|---|---|
| modeling | `prepared-scene/1` | `pipeline-model/1` |
| simulation | `pipeline-model/1` | `pipeline-simulation/1` |
| rendering | `pipeline-simulation/1` | `pipeline-presentation/1` |

`engine_contract=physics-python/1` 固定接口兼容，不等同于所有引擎版本数值结果相同。摘要证明包身份；本机操作者仍负责评估发布代码。独立 `stage-module` 不能在 QQ 直接激活，必须构建新组合。旧任务继续使用原组合；旧版本自动 GC 尚未实现。

可选本地流匹配运行时另由宿主保存 runtime/config pin，权重和 Python 运行时不塞进反复计算摘要的 QQ 组合包。其任务推理没有网络下载权限。语言模型只选当前事件 `image_id` 与物理假设；私有输入路径和解释器由可信宿主/适配器分配。图片生成成功后仍需插入 authored scene 并 prepare，生成回执本身不是模拟准备或数值验证。

## API、prepare 与设置

`main` 验证 task v1 固定工作/产物目录、network=false、字节限额和可空时间预算，然后以非阻塞文件锁 `work/pipeline/.execution.lock` 串行处理当前任务调用。

适配器增加 `pipeline_capabilities`、`pipeline_configure`、`pipeline_status`、`pipeline_render_prepare`，兼容已有领域工具，并提供可选的 `modeling_from_image`。能力返回实际安装的阶段和质量；当前标准建模、visual/strict 模拟与 preview/standard 渲染互相独立。未安装 high/deep 显式失败，不能静默降档。

普通 prepare 由固定引擎执行，成功后 `freeze` 保存 `pipeline-plan/1`：规范化 prepared 模型摘要、完整组合摘要、质量设置、预计耗时和 mode；`plan_id` 绑定这些字段。`check_plan` 在执行前重算，不接受过期准备或修改后的设置。修改模型、patch、example、system 或配置使准备失效。

数值问题必须在运行前声明 queries。模拟选择 `inherit` 时保留模型 validation；明确 visual/strict 时在 prepare 之前写入预算选择。渲染档位不会更改物理窗口、模型材料或求解精度。required-data 组合在缺少宿主 send_file 能力时，prepare/执行不会被当作可交付成功。

## 固定 argv 与时间预算

`stage` 只写 `stage-call/1` request，使用当前解释器、固定 module entrypoint、engine/request/response 参数启动进程。stdout/stderr 留在任务调用日志；worker 按角色调用预声明函数，不把外部文字解释成命令。

宿主沙箱把网络禁用，业务可写范围限制到当前任务；可信包、系统运行时和可选本机模型安装目录可读。模块不是独立的 OS 权限沙箱：它们继承宿主为整个任务设定的约束。进程组管理、发送和外部平台交互由 QQ 宿主负责。

有限预算时 `remaining` 从整个 run 开始计时，阶段子进程不能越过剩余墙钟预算。`wall_time_seconds=null` 原样沿用，不能暗中变成 180/300 秒。单视频/数据字节限额继续生效；当前无硬聚合 CPU、内存或临时磁盘配额。

## Checkpoint 与恢复

每个模型/模拟成功响应由 `seal_checkpoint` 绑定响应 metadata、模型/result/manifest 路径及 SHA-256，并把模拟 manifest 声明的全部 artifacts 加入文件清单。`checkpoint_sha256` 绑定整个封存响应；恢复的 `verify_checkpoint` 同时重算 metadata 与文件摘要。质量声明或路径改变也会被发现，不能仅凭 `ok=true` 复用。

任务状态在 `work/pipeline/state.json` 保存 `pipeline-state/1`：compute_key、plan_id、bundle、阶段结果和 status。compute_key 绑定 prepared 模型、引擎及建模/模拟组件；渲染呈现选择可在相同计算结果上变化。

| 已保存状态 | 行为 |
|---|---|
| 成功模型 checkpoint | 校验后复用，不重建模型 |
| 成功模拟 checkpoint | 校验后复用，不再次积分 |
| 非空模型/模拟目录，但缺少有效封存 | `stage_incomplete`，保留证据，不覆盖或盲目重跑 |
| prepared / compute_key 已变 | 原模型、数值目录与 state 移到任务 history，再开始新尝试 |
| ZIP 导出或渲染失败 | 保留成功模拟与具体 failed_stage，允许经准备的呈现恢复 |
| 源文件、checkpoint 或组合被篡改 | 拒绝复用，不能重写摘要掩盖变化 |

`pipeline_render_prepare` 要求当前任务已有有效模拟 checkpoint，固定 `mode=render_only` 和 checkpoint 摘要，再用原 execution 入口执行。run 跳过建模/模拟，仅重新导出和渲染。该路径只支持当前任务及原组件 pin，不提供任意旧任务路径导入或跨 renderer 版本迁移。

## 最终结果与交付边界

导出在渲染前完成；超限 data.zip 明确失败，不能为了发送而隐式丢弃数据。最终成功前再验证模拟 checkpoint 和 MP4 / ZIP 摘要，写 v1 `result-manifest.json`，包括：

- 单一 MP4 output 和至多一个 ZIP attachment；
- 独立 delivery / numerical 状态、precision warnings 和固定 validation 模式；
- 完整组件锁、plan_id、模拟摘要及请求需要的经过质量门的数据回答。

`verification.passed=true` 表示模块交付门通过，不能把 visual 预览变成 quantitative 认证。宏步观测与采样显示状态明确分开。宿主继续核验 paths/hash/bytes，再从 held 放行并记录真实回执；适配器只写“等待宿主交付”。unknown 回执不在模块中重放。

失败保存 state.failed_stage，并输出 `pipeline_stage_failed`、具体阶段响应及是否保留成功模拟。structured failure 不是自动重试授权；未变化的失败模型不应反复运行。

## 验证与扩展规则

组合/阶段回归分别在 `toolboxes/tests/test_pipeline_bundle.py`、`test_pipeline_stages.py`、`test_pipeline_presentation.py`；真实无网络沙箱 smoke 由 `pipeline_smoke.py` 完成。物理求解与渲染测试另在根 `tests/`，QQ 授权/交付回归在客户端。GPU 实际推理及平台真实回执需要独立证据。

新增阶段质量或能力必须同时更新：模块实现、module metadata、组合能力发现、Skill、内部架构和对应真实回归。不能增加未实现的 capability、绕过既有数值/视频门，或让呈现代码拥有再积分权限。内部说明见 [建模](manual/modeling/architecture.md)、[模拟](manual/simulation/architecture.md) 与 [渲染](manual/rendering/architecture.md)。
