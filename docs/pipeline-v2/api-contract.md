# Pipeline v2：API、执行与恢复契约

状态：**设计草案，未实现、未注册、未部署**。本文件定义未来 host 与三类 toolbox 的共同边界。
现有 v1 入口、`schema_version=1`、单 MP4 验证和旧任务 pin 保持不变；不能把这些操作当作当前可调用工具。
总体取舍见 [规划书](README.md)，产物字段见 [产物契约](artifact-contract.md)，机器定义见 [contracts.schema.json](contracts.schema.json) 与 [api-tools.json](api-tools.json)。

## 1. 调用者与引用

客户端 agent 只向 host 暴露的结构化工具传参。host 负责权限、schema、版本 pin、预算、路径与资源验证，再派发给模块。
模块拥有领域算法和验收规则，不拥有 QQ、其他消息通道、凭据或任意文件访问权限。
普通流程用 host API；阶段 API 用于单独建模、查询、续改和重渲染，不能绕过同一套策略。

`plan_id`、`job_id`、`model_ref`、`simulation_ref`、`render_ref` 都是宿主签发的不透明引用。
引用绑定作用域、不可变内容 hash、生产者 pin 和依赖；客户端不能通过猜 ID、传本地路径或 URL 获得读取权限。
旧模型、模板说明、导入文件和 provider 响应都是数据，不具有指令或授权效力。

“上次/昨天”由 host 会话上下文提供有限的已授权候选引用及摘要，客户端只能选择这些引用；缺失或歧义返回 needs_input，不能猜 ID。`PipelineRequest.reuse` 记录重用的模型/模拟/渲染引用，`permitted_stages` 冻结允许执行的阶段。禁止重算时只允许 rendering；输入不足就报告缺口。独立 rendering API 同样没有启动 simulation 的权限。恢复已接受的作业用原 job_id，不重新 prepare。

## 2. 冻结的公共操作

下表是设计中的完整公开操作名；参数结构、必填项和枚举以机器定义为准。质量是独立维度，不由渲染器决定求解精度。

| 操作 | 作用与成功响应的主要内容 |
| --- | --- |
| `pipeline_prepare(request)` | 协商三阶段、质量、状态导出及交付能力，返回 plan、假设、预估与缺失项 |
| `pipeline_submit(plan_id, idempotency_key)` | 提交冻结计划，返回 host `job_id`；异步推进建模、模拟、渲染 |
| `pipeline_status(job_id)` | 查询整个任务或单阶段任务的状态、进度、产物引用和下一步 |
| `pipeline_cancel(job_id, idempotency_key)` | 请求停止未开始阶段和正在运行的作业，并核对实际取消结果 |
| `pipeline_deliver(job_id, idempotency_key)` | 按已准备的交付计划排队产物，返回逐项交付状态和回执 |
| `modeling_capabilities()` | 公布 domain、模板、输入类型、质量、限制与协议支持 |
| `modeling_template(template_id)` | 返回参数 schema、单位、范围、默认值、假设和最小示例 |
| `modeling_prepare(spec, quality)` | 校验并规范化建模要求，准备不可变计划；质量为 `standard|high` |
| `modeling_build(plan_id, idempotency_key)` | 异步生成模型和双资产映射，返回 host `job_id` |
| `modeling_inspect(model_ref)` | 检查模型依赖、几何审计、实际精度与模拟适用边界 |
| `simulation_capabilities()` | 公布模型类型、求解器、观测、状态 profile、数值门与限制 |
| `simulation_prepare(model_ref, spec, quality)` | 准备步长、时长、观测、状态及验证计划；质量为 `visual|strict` |
| `simulation_run(plan_id, idempotency_key)` | 异步求解并导出数据，返回 host `job_id`；成功不依赖视频生成 |
| `simulation_query(simulation_ref, query_id)` | 读取已有且满足验收条件的预声明查询，不暗中增加采样或重算 |
| `simulation_inspect(simulation_ref)` | 返回时间范围、采样语义、模型适用性、数据完整性和验证结论 |
| `rendering_capabilities()` | 公布输入状态、几何映射、provider、格式与视觉质量能力 |
| `rendering_prepare(model_ref, simulation_ref, spec, quality)` | 核对依赖并准备镜头、时间映射和输出；质量为 `preview|standard|deep` |
| `rendering_render(plan_id, idempotency_key)` | 异步读取已保存数据并渲染，返回 host `job_id` |
| `rendering_inspect(render_ref)` | 返回输入 hash、帧覆盖、时间映射、解码与实际质量报告 |

只读操作没有幂等键。四个阶段性提交入口与取消、交付使用宿主签发的幂等键；agent 不自行更换键来“重试”。
`pipeline_submit`、`modeling_build`、`simulation_run`、`rendering_render` 均快速返回作业引用，不在短 API 调用内等待计算完成。
所有阶段作业统一经 `pipeline_status(job_id)` 查询和 `pipeline_cancel` 取消，不另增加一套模块轮询 API。
独立渲染必须证明两个输入来自一致的模型依赖；不一致返回错误，不能靠外形相似自动拼接。

## 3. AgentResult/2：统一响应

所有公开操作返回同一 envelope。`data` 始终是对象，`errors` 始终是数组，未使用时分别为 `{}` 和 `[]`。
工具应返回短摘要与可执行的最小下一步；大模型、顶点和逐帧数据留在 bundle 中。

```json
{
  "schema_version": "agent-result/2",
  "ok": true,
  "status": "queued",
  "stage": "simulation",
  "summary": "模拟任务已排队。",
  "data": {"job_id": "job-example-001"},
  "errors": [],
  "next_action": {
    "kind": "poll",
    "tool": "pipeline_status",
    "arguments": {"job_id": "job-example-001"},
    "poll_after_ms": 1000
  }
}
```

示例 ID 与等待时间只用于说明格式。`ok=true` 表示本次调用被接受并返回有效结果，不表示计算、质量或远端交付完成。
受阻或失败的操作返回 `ok=false` 与可解释错误；判断任务完成必须同时读取 `status`、验证门和交付项。
`stage` 仅取 `host|modeling|simulation|rendering|delivery`；`status` 仅取下一节的枚举。
错误至少应说明稳定 code、字段或阶段、可解释原因、是否可修复及允许的修复方向；不得把原始日志、凭据或任意 provider 文本直接当用户回复。

`next_action.kind` 仅取 `call_tool|poll|respond|none`；`tool` 为工具名或 null，`arguments` 为对象，`poll_after_ms` 为整数或 null。
`call_tool` 必须有工具名及符合该操作 schema 的参数；`poll` 只能指向 `pipeline_status`，并给出正整数等待时间。
`respond|none` 使用 `tool=null`、`arguments={}`、`poll_after_ms=null`；`respond` 表示向用户解释结果或收集明确缺失项。
host 必须在 dispatch 前重新验证 next_action 的操作白名单、参数、作用域和预算；它不是模块获得执行任意工具权限的渠道。

## 4. 计划准备与版本冻结

prepare 先解析明确需求，再应用模板默认值；保留用户明示参数与假设来源，不用默认值覆盖明确要求。
完整计划包含不可变输入及 hash、三模块 pin 或所需阶段 pin、plan hash、请求/拟执行质量、数据 profile、观测、验证门和交付项。公开 Plan 用 `effective_arguments` 保存规范化参数，`effective_limits` 保存实际约束，`delivery` 保存交付目标；`quality_resolved` 是计划选择，不是已经验收的实际质量。plan hash 对 host 封存的规范计划原始字节计算，在 StageTask 中引用，不把自身 hash 写入待散列的计划。
`Plan.permitted_stages` 不得超出请求；重用引用必须与用户未修改的模型/参数相符。required 输出需要被禁止阶段重算才能获得时返回 unsupported，而非扩大允许阶段。独立阶段 prepare 从 host 请求上下文继承预算与交付偏好，并将其摘要回显；无上下文时采用公布的本地默认策略，不猜授权。
计划也包含时间/计算预估、费用范围与上限、工作磁盘/状态/最终产物预估、允许的退化选项、有效期和 host 策略版本。
计划与提交所需幂等键通过响应 `data` 返回；agent 只传回 plan_id，不能在 submit 时夹带新参数。
接收作业后，host 在 status data/control_keys 中提供该 job 的取消与交付幂等键，并可给出相应 next_action；这些键绑定操作和原作业，客户端不自行生成。单阶段作业若未准备某种交付能力，deliver 返回具体限制，不自动扩大原计划。

prepare 对流水线须先协商 rendering 所需状态，再准备 simulation；不能算完抽样显示帧后才承诺 full_state 或更高采样精度。
prepare 不是授权或资源承诺：submit 需再次检查权限、配额、资产、包完整性、plan 有效期，并原子保存 pin、plan hash 和幂等记录。
`pipeline_prepare` 只读一次 `v2/active-bundle.json`；默认三阶段任务的三 pin 必须来自同一个兼容组合。独立阶段或明确只需部分阶段的作业只锁所需模块，不要求未使用 renderer 存在；已有输入的 producer pin 从 bundle 保留。建模计划的 state_plan 为 null，阶段质量只列相关维度；不能填造后续阶段的实际质量。
active 更新本身不使未过期且可用的旧 plan 失效。缺包、资产过期或策略收紧返回 `plan_expired` 或 `capability_unavailable`，要求重新 prepare。
这些是错误 code，不增加新的 status。不能悄悄改 pin、模型、质量或输出继续执行。
已接受任务写入 `pipeline-lock.json`；重启、恢复和重试均读该锁，不重新解析 active。

## 5. 状态机与完成语义

| status | 含义与允许的下一步 |
| --- | --- |
| `capabilities_ready` | 能力已读取，可选择模板或 prepare |
| `template_ready` | 模板已读取，可按 schema 填参并 prepare |
| `plan_ready` | 计划已冻结且当前可提交；下一步为对应提交操作 |
| `needs_input` | 缺少影响任务含义的参数；返回具体字段，再 prepare |
| `needs_authorization` | 已有授权不足以覆盖本次上传、付费或范围；不能先提交 |
| `unsupported` | 当前已公布能力不满足要求；返回具体缺口与可选能力 |
| `over_budget` | 计划或运行不能满足费用/资源上限；返回可行调整，不暗降质 |
| `queued` | 已持久接收，等待执行；轮询或取消 |
| `running` | 执行或取消处理中；轮询，不能当已取消或已完成 |
| `reconciling` | 远端提交、取消或投递结果未知；核对既有动作，禁止盲目再发 |
| `succeeded` | 本作用域全部必需产物或交付项已通过对应完成门 |
| `partial` | 仅在计划明确允许部分结果时使用，逐项列出完成与缺失 |
| `failed` | 本次作业无法完成，保留已验证资产及结构化原因 |
| `cancelled` | 已确认计算停止或未开始动作已撤销；不表示既有计费自动退款 |

典型计算转换为 `plan_ready → queued → running → succeeded|partial|failed|cancelled`；外部结果未知可进入 `reconciling`，核对后再到运行或终态。
终态作业不可原地改为另一份模型或重新运行；修复产生新 plan 和新作业，并保留父作业关联。
`pipeline_status` 分别报告计算与 delivery 状态。计算 succeeded 只表示产物已验证；`stage=delivery,status=succeeded` 才表示全部必需交付项达到通道声明的回执门。
默认 video 和 data 均 required；缺一项不能标整个交付成功。未允许 partial 时保留可用资产并报告失败阶段。
取消请求返回 running 时只是已接收取消意图；provider 不支持取消或状态未知时必须明示，不能立刻报 cancelled。

## 6. 幂等、恢复与有限修复

host 持久绑定调用者作用域、操作、幂等键、规范化参数 hash、plan hash 与结果引用。
相同键与同参数返回原作业/原交付结果；相同键不同参数返回 `idempotency_conflict`，不创建副作用。
网络断开后先用原键恢复响应或查询原 job_id；尚无 provider_job_id 时也不得凭超时认定未提交。
provider 缺可靠幂等或查询能力时，未知提交留在 reconciling 并报告可核查限制，不自动创造第二个付费任务。
重复回调、轮询终态和重复 deliver 不能重复发布产物。交付沿用 `(delivery_id, artifact_id, destination)` 身份；unknown 回执不自动重发。

agent 对可修复领域错误最多做两次有实际参数变化的修复，每次重新 prepare；次数不是允许改变用户明确要求的授权。
host 记录 repair lineage，防止以新 plan 绕过上限；不能对未改变的失败模型反复计算。
传输退避/状态查询由 host 在预算与截止条件内处理，不消耗这两次模型修复，也不能无限重提交。
质量门失败保留诊断，不能删检查、伪造成功或自动把 strict 降为 visual。
render 失败保存 SimulationBundle；只改镜头/材质/编码时复用其原 hash，不重跑求解器。

## 7. toolbox 进程协议

v2 包应包含入口、依赖声明/锁、操作 schema、能力声明、validator 与 skill；内容摘要覆盖这些文件。
host 校验可信安装与包完整性后，在隔离执行器中用固定参数向量启动进程，不导入模块源码到消息服务进程。
领域 API 的实现可以由该包入口调用包内代码；它不要求模块导入 host 或消息 SDK。

```text
python PACKAGE/ENTRYPOINT --phase probe --task JOB/stage-task.json
python PACKAGE/ENTRYPOINT --phase api --task JOB/stage-task.json --request JOB/request.json
python PACKAGE/ENTRYPOINT --phase run --task JOB/stage-task.json
```

`probe|run` 保留固定启动形态，但 v2 任务使用独立 `StageTask/2`，不能发给 v1 入口。probe 是有界预检，run 是已接收阶段的计算执行。
StageTask/2 仅由 host 写入，包含作业/阶段、已锁 plan 与 pin、获准输入引用及只读挂载、输出/work 目录、资源/费用约束和执行权限。
不把用户提供的 task JSON、路径、环境变量或 shell 片段直接作为 StageTask。任务中不存凭据或无关身份数据。
`--phase api` 的 request.json 由 host 根据公开调用写入，包含操作及通过 schema 校验的 arguments；入口原子写 `JOB/api-result.json`，内容为 AgentResult/2。
host 为并行调用分配独立 stage 调用目录或使用锁，防止覆盖 request/result；校验响应大小、schema 与目录边界后返回 agent。
run 从不可变计划读取工作内容，并原子发布阶段结果；stdout/stderr 仅为有界脱敏诊断，不作为成功协议。
进程参数始终是 argv 数组，不能把自然语言拼成 shell；输出目录逃逸、软链接、特殊文件或 hash 不匹配均拒绝。
进程退出 0 不是领域成功，非零退出也不能覆盖已持久记录的远端未知状态；恢复必须核对阶段记录。

## 8. 资源与外部 provider broker

host 分别约束计算时间、并发、内存、临时磁盘、状态量、单文件/总输出字节、外部调用次数与金额；能力中明确哪些有硬隔离实现。
`wall_time_seconds=null` 只表示没有固定墙钟期限，不解除其他上限；发布新 executor 前不得声称 macOS 已有硬内存/CPU 配额。
预算不足返回 over_budget；取消、失败和超额保留已确认成本以及可验证数据。任何降采样或显示压缩仅在冻结计划允许范围内进行。

请求的 requested_budget 仅为用户上限偏好，不能赋予权限；host 取用户上限与现有策略的交集。无硬限额的资源必须在 effective_limits.hard_enforced 中如实缺席，并说明准入估计与运行监测方式。

`SimulationSpec.mode=steady` 时 physical_duration_s/observation_interval_s 为 null；瞬态二者为正数。是否支持稳态由 domain 能力决定。`RenderSpec.template_id + parameters` 只能引用 rendering_capabilities 公布的模板及参数 schema，用于镜头、灯光、外观材质等，不能传任意 provider 配置。模型/查询的动态参数同样要按所选模板/metric 再验证；通用 schema 中的参数对象不是允许未知参数的例外。

provider adapter 的最小内部操作为 `capabilities / estimate / submit / status / cancel / fetch / verify`；它不是 agent 的额外公共工具集。
adapter 公布输入/output profile、版本、幂等与取消能力、计费及保留语义；不把任意 URL、模型名或供应商参数透传为可执行权限。
toolbox 保持 network=false；独立 broker 持有 secret ref、服务 allowlist、任务范围 handle、获准上传资产清单与预算。
broker 在提交前保存请求摘要和幂等记录，持久化 provider_job_id、状态及已知成本；恢复使用原记录核对。
fetch 由 broker 限制下载域名、重定向、大小与内容，完成后将文件交 host 和领域 validator 验证，不信任远端 success 字段。
保真渲染须验证实体及时间映射；生成式结果标记 illustrative，不能替代科学视频或数值数据。无法 pin 的远端版本声明 best_effort 重现性。
沿用已有授权；仅缺失上传/付费权限或超范围时返回 needs_authorization。本地已授权流程不新增统一确认门。

## 9. 实施验收门

必须覆盖 v1 回归、混合版本拒绝、旧 plan 提交、重启 pin 不变、同键重复/冲突、跨作用域引用和响应 next_action 注入。
故障用例覆盖提交未知、重复回调、取消未确认、费用超限、renderer 失败不重算、required 数据缺失与多项交付部分成功。
每个用例分别验证任务状态、canonical hash、实际副作用次数、费用记录与回执；工具返回成功不能替代这些证据。
在以上门通过前，本契约只作为评审与后续实现依据，不代表任一模块、外部 provider 或文件通道已接通。
