# 当前整体架构

项目由私有 QQ 宿主、可独立发布的三阶段 toolbox 和物理引擎组成。QQ 承担通信、权限和交付；建模解释与几何处理、模拟的状态推进、渲染的数据呈现分别由模块承担。当前对 QQ 使用稳定 v1 合同，`docs/pipeline-v2` 中的异步多产物接口是后续设计。

```text
已授权 QQ 事件
  → transport / policy → 持久 inbox + toolbox pin
  → 当前事件图片 / 引用 / 转发规范化 → 受限 Agent
  → v1 toolbox adapter（组合版本 + bundle-lock）
      → 建模模块 → 明确尺度/材质/拓扑的准备模型
      → 模拟模块 → 原引擎数值结果、预声明观测和采样显示轨迹
      → 渲染模块 → 经解码的视频 + 精确结果数据 ZIP
  → 宿主路径 / 哈希 / 大小 / 验证声明检查
  → held 成功事务或唯一失败回复 → outbox → 平台独立回执
```

## 代码层与数据所有权

| 层 | 权威位置 | 责任 |
|---|---|---|
| QQ 宿主 | 独立安装的通信桥接，不随此仓库发布 | 传输认证、授权、去重、队列、附件隔离、受限 Agent、结果检查与回执 |
| v1 组合适配器 | `toolboxes/pipeline/adapter.py`、`bundle.py`、`worker.py` | 固定组件组合、阶段调用、准备模型一致性和恢复 |
| 建模阶段 | `toolboxes/pipeline_stages/modeling.py`；`toolboxes/pipeline/manual/modeling/` | 几何/结构化参数与审计；不代替数值求解，不假设未给定物理尺度 |
| 模拟阶段 | `toolboxes/pipeline_stages/simulation.py`；`toolboxes/pipeline/manual/simulation/` | 原物理引擎调用、验证与保存数据；不以视频反推测量 |
| 渲染阶段 | `toolboxes/pipeline_presentation/`；`toolboxes/pipeline/manual/rendering/` | 从已验证结果渲染并导出；不回写求解状态 |
| 引擎 | `physics_demo/` 与 `toolboxes/physics/` 快照 | 场景校验、规划、核心物理、查询、数值质量门、轨迹 |
| 构建发布 | `toolboxes/build_pipeline.py`、`toolboxes/registry.py` | 独立 stage 构建、组合打包、摘要发布和原子激活 |
| 本机记录 | 客户端 runtime 下 `records/`、SQLite 与证据 | 原始消息、实验、失败、反馈、回执和小型证据；不进入公开包 |

引擎内部职责和 C ABI 的权威说明是 [引擎架构](architecture.md)。公开的宿主输入、版本固定、隔离与数据交付合同见 [宿主接入说明](../toolboxes/pipeline/HOST-INTEGRATION.md)；具体客户端线程、授权和通信配置由独立宿主维护。阶段内部协议及构建方法见 [pipeline](../toolboxes/pipeline/README.md)，各阶段 Skill 与代码随对应模块一起发布。

## 热插拔与状态约束

发布版本放在 registry 的 `versions/<sha256>`；发布检查软链接、特殊文件、入口和内容摘要，随后原子替换 `active.json`。入队前捕获已验证的 active 快照并保存 registry、digest、id、version，任务开始后使用该 pin。活动指针切换只影响未来事件，已有任务不会混用新版本或变更 registry。

组合包中的 `bundle-lock.json` 另绑定引擎和建模、模拟、渲染各模块的 id、version、路径、摘要和阶段格式：

| 阶段 | 输入 | 输出 |
|---|---|---|
| 建模 | `prepared-scene/1` | `pipeline-model/1` |
| 模拟 | `pipeline-model/1` | `pipeline-simulation/1` |
| 渲染 | `pipeline-simulation/1` | `pipeline-presentation/1` |

独立 `stage-module` 只作为可组合部件存储，不能作为 QQ 任务直接激活。更换单阶段需要构建新组合、验证并激活组合摘要；回滚激活旧摘要。旧版本在任务仍引用时保留，目前没有自动版本 GC。

`prepare` 固定规范化模型与质量选择，随后执行必须检查准备摘要、组合摘要和设置一致。变更模型或选择参数使准备失效。模块在独立固定 argv 子进程中运行，业务文件只能写当前任务，默认没有网络。进程可以读取可信程序、系统运行时和当前任务；来信不能选宿主路径、解释器或新命令。

## 图片建模与本地模型边界

客户端输入层已能接收真正的 JPEG / PNG 像素、同群引用及显式合并转发，模型看到事件 ID 和图片标识，宿主保留私有路径。图片解析、视觉语言解释、生成式单图到 3D 是不同能力。单图几何存在不可见面和尺度歧义；必须记录来源、生成假设、网格质量和用户指定物理参数，再通过既有场景准备及求解质量门。

Mac 本地公开流匹配后端属于可选建模运行时，代码与权重由本机操作者配置并固定；运行时安装、权重下载和任务推理分开。离线任务不得隐式下载权重或调用云端。QQ 来信只能选当前事件图片和明确建模参数，不能给任意路径或子进程命令。当前已在 16 GB M4 Mac 上完成真实 JPEG→U2Net CPU→Hunyuan MLX Metal→几何/自交检查→原引擎模拟→MP4/ZIP 的 deny-network 沙箱验收。具体固定版本、样例与限制见 [验证记录](modeling-flow-validation.md)；一个样例成功不保证任意照片都生成可仿真网格。

## 交付、恢复与验证

客户端在计算前预留 held 回复和视频容量，不发进度。成功需要数值、完整视频解码、路径/大小/哈希及必需数据全部通过后，原子生成可发送计划和产物。失败同事务取消未发送成功计划并排一条具体原因。平台回执 sent、failed、unknown 分别保存；unknown 不重发，也不当作成功实验。

模拟成功结果独立保留，渲染失败可在原任务与原组件 pin 下重做呈现而不重算物理。未完整的非空阶段目录不会盲目覆盖。实验归档只保存已经真实交付的模型和参数；同群、同发送者与模块范围的续改形成新任务。反馈与本机复测没有发送旧请求或修改来信权限的能力。

当前默认交付一份 MP4 和一份至多 16 MiB 的 ZIP；超限完整数据明确失败，不静默抽样。宏步观测是测量数据，显示轨迹是采样展示，两者语义分开。无固定时限的 OneBot 路径仍有接收容量和单产物字节限额，没有硬聚合内存或临时磁盘配额。

客户端 unittest、模块合同/数值测试、真实 macOS deny-network 沙箱 smoke、公开模型实际推理与 QQ 真实回执验收分别验证不同层。源码变更不能冒充平台上线；本地假服务回执不能冒充 QQ 送达。维护时优先沿用稳定 v1、既有工具与归档格式，新增能力采用可选发现和具体失败结果。

完整数据包可包含经来源 pin 校验的 GLB、OBJ、网格 JSON 与四份第三方许可。大包按需无损压缩，成员原始字节不变，宿主不解压/执行。旧引擎的模型序列化缩进由组合适配器规范化，仍保留原 1 MB 模型限额和 mesh_ref 插入顺序摘要。
