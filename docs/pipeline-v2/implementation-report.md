# QQ 热插拔实施与 1.4.0 兼容验收

本文件保留 0.1 阶段的实现/验收记录。当前 0.2 本地图片建模、双网格、来源审计和无损导出见 [当前整体架构](../current-architecture.md) 与 [pipeline](../../toolboxes/pipeline/README.md)；v2 通用异步多产物合同仍未上线。
2026-09-26。实现分支：`qq-pipeline-compat/1`，组合包版本 `physics-pipeline 0.1.0`。求解器仍是原 `physics 1.4.0`；没有为实现阶段接口而修改求解器算法。

## 架构检查结论

原架构能热换一个完整 toolbox，但不能直接满足三模块独立替换、原子锁版与数据文件投递。本次补齐“独立阶段模块 + 原子组合包”：建模、模拟、渲染可以单独构建和选版，QQ 任务一次 pin 固定整个组合及其引擎依赖。新任务使用新组合；已有任务与恢复沿用原 digest。更换模块后重新组合、验收和激活，不需要重启 QQ。

数据附件需要 QQ 宿主首次升级并启用 `send_file`。这次完成的是源码、可构建包及离线验收，没有升级运行中的宿主、改变线上 active、发送 QQ 消息或调用商用 API。因此本报告不宣称已完成 QQ 平台端到端交付。

## 已实施内容

| 部分 | 实现 | 验收要点 |
| --- | --- | --- |
| 独立建模 | `pipeline_stages/modeling.py` | 校验显式模型，生成不可变 `pipeline-model/1`；绑定引擎身份和执行限制。 |
| 独立模拟 | `pipeline_stages/simulation.py` | 复用原 1.4.0 mechanical/PCB；`make_video=False`，保存原结果、预声明观测与质量门。 |
| 独立渲染/导出 | `pipeline_presentation/` | 保存状态插值、自动取景、H.264 完整解码；原始 JSON、CSV、字典、质量与 hash 数据包。 |
| 组合和热插拔 | `build_pipeline.py`、`pipeline/bundle.py` | 模块角色、输入/输出、引擎契约、禁网要求和摘要校验；旧包与任务 pin 不变。 |
| 客户端编排 | `pipeline/adapter.py` | 兼容原建模/准备/执行/查询 API，增加 capability、独立质量选项、状态和只重渲染入口。 |
| 运行技能 | `pipeline/manual/*/SKILL.md` | 三阶段短流程、能力发现、参数冻结、数据质量解释、有限恢复及真实回执要求。 |
| QQ 双交付 | `qq-simulator-agent-opencode` 的数据附件、服务、outbox、transport、cleanup | MP4+ZIP 原子入队，message_id/file_id 独立收据，unknown 不重发，未完成数据保留。 |

保存的 `result.json` 逐字节进入 ZIP。1.4.0 的查询假设中存在依赖字段顺序的表示，不能为了 JSON 排序而重写原结果。显示帧有抽样/舍入，不能用它们伪造完整求解状态；预声明 observations 单独以求解宏步导出。

模拟质量状态在阶段间只可保持或收紧，不能因为重新渲染而被升级。没有通过数值门的数据必须标记为诊断数据。重渲染前后对规范结果、模型、观测与阶段记录复核摘要；成功模拟不依赖渲染器是否最终成功。

## 真正使用的 1.4.0 基线

测试输入为本机内容寻址 registry 中的原始已发布快照：

```text
id: physics
version: 1.4.0
digest: 097cc92fa3f59da97051523b9758f8abeb9071c446476f5f66c90a284dc6278d
```

组合器复制后的引擎目录摘要与上述发布摘要完全一致。验证不依赖仓库当前版本字符串，也没有将 1.4.2 重新标成 1.4.0。当前 1.4.2 的独立活动快照保持原样。

## 已验证行为

- 机械与 PCB 在真实 macOS QQ 禁网沙箱中完成 prepare、独立建模、无视频模拟、数据导出和 H.264 视频；宿主实际产物验证器接受 MP4 与 ZIP。
- 原 `physics_query` / `pcb_query` 仍可读取结果。
- 重渲染后原始结果与求解阶段响应的 hash、mtime 均未变化，确认没有再次求解。
- 替换单独模块并激活新组合后，旧组合 pin 仍通过完整校验。
- 篡改模块、模型、冻结参数、checkpoint 元数据或数据文件被拒绝；缺少数据发送能力时在求解前拒绝。
- 注入渲染/导出失败后复用已封存模拟；封存前崩溃留下的输出和成功响应保留，不能直接覆盖重跑。
- 双交付离线测试覆盖原子回滚、独立收据、视频失败而数据成功、文件回执未知不重发、清理保留及发送前 hash 复查。

可重复验收命令与产物位置见 [运行说明](../../toolboxes/pipeline/README.md)。`pipeline_smoke.py` 把实际引擎/组合 digest、每领域查询结果、重渲染不变检查及本地视频/ZIP 路径写入 `acceptance.json`。单元测试为 `toolboxes/tests/test_pipeline_*.py`，宿主测试见 `tests/test_data_delivery.py` 与相关回归。

最终流水线回归：真实 1.4.0 与当前 packaged engine 各 **39 项通过**（17 项组合/恢复、11 项建模模拟、11 项渲染导出）。三份运行 skill 均通过 frontmatter 校验；原 v2 契约的 11 份正例、19 个签名与 14 项反例继续通过。

QQ 宿主最终 **140 项回归通过**：`test_data_delivery`、`test_onebot`、`test_held_storage`、`test_experiments`、`test_official`、`test_toolbox`。使用已安装的 OneBot Python 依赖环境运行，临时 localhost 假服务与子沙箱获准执行；没有连接真实 QQ。测试输出有一条 SQLite fixture 关闭时的 ResourceWarning，无测试失败。此前系统 Python 缺少 aiohttp 和外层沙箱限制的环境错误，在这轮完整环境复测中已排除。

本机最终沙箱证据保存在 `runs/pipeline-acceptance-1.4.0/`（运行产物，不纳入 Git）：

- 总验收 JSON（本机历史验收文件，不随公开源码发布），组合 digest 为 `c48d1fc35704d0483b33c86ba1758b8942b766fe84ca1914a48bbc6aa0ebb760`。
- 机械：视频（本机历史验收文件，不随公开源码发布）、数据 ZIP（本机历史验收文件，不随公开源码发布）。
- PCB：视频（本机历史验收文件，不随公开源码发布）、数据 ZIP（本机历史验收文件，不随公开源码发布）。
- `source/` 是可发布的组合包；`registry/` 是本次隔离测试注册表。样例的最后一步是 preview 重渲染；正式默认档位仍为 standard。

同一组合又完成了 1.4.0 原生后端沙箱验收（本机历史验收文件，不随公开源码发布）：使用原预编译 native 求解器，机械与 PCB 双交付、查询和只重渲染同样通过。前一份验收使用 Python 后端；两者没有更换组合或引擎版本。

## 本轮能力边界与下一阶段

本轮实际开放 `standard` 建模、`visual/strict` 模拟、`preview/standard` 渲染，默认交付科学视频和完整受限数据 ZIP。没有实现高精 CAD、PBR 深度渲染、完整状态重启、跨任务重渲染迁移、自动引用计数 GC 或商用网络 broker。高精/深渲请求返回明确 unsupported，不能静默降档。

外部生成式接口当前只有可校验的请求/回执契约，没有提交、上传、扣费或下载能力。[商用扩展方案](external-rendering.md) 已核验官方接口，给出预算预留、提交未知状态去重、源数据保护、额外艺术视频角色和上线门禁。生成视频不替代数值证据。

下一步上线是一次宿主升级与 `send_file` 启用，再激活已验收组合；随后需分别取得真实新视频回执与群文件 file_id，确认视频播放和 ZIP 下载/读取。现有 unknown 动作、历史队列和水位不能重放或改写。部署步骤见 [公开宿主接口合同](../../toolboxes/pipeline/HOST-INTEGRATION.md)。
