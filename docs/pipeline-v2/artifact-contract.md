# 产物契约：模型、模拟、渲染与交付

状态：设计草案，尚无 v2 runtime。本文中的 MUST/必须是后续实现验收要求，不代表当前产物已满足。2026-10-13 复核增加显式用途、强类型 metadata/GeometryBinding 与 engine dependency lock；仍未实现 v2 runtime。

## 1. 通用 envelope 与引用

三个 bundle 使用共同 `BundleManifest` envelope；内容类型分别为 `ModelBundle/1`、`SimulationBundle/1`、`RenderBundle/1`。机器字段在 [contracts.schema.json](contracts.schema.json)，示例在 [examples](examples)。envelope schema 检查结构；本文件定义必须另做的文件与领域语义验证，不能只通过 JSON Schema 就声称物理合格。

每个 bundle 包含：`schema_version`、`bundle_id`、`run_id`、`producer`（模块 id/version/digest）、`inputs`（上游 bundle refs）、`files`、`quality`、`validation`、`metadata`。metadata 通过 kind 分派到 ModelMetadata、SimulationMetadata、RenderMetadata，禁止未知字段；领域正确性仍需独立 validator，不是任意物理语义的授权。

quality.requested 可保留流水线意图；quality.actual 只记录本阶段与已验收祖先阶段。ModelBundle 不声明模拟/渲染实际质量，SimulationBundle 不提前声明渲染实际质量。计划的 quality_resolved 与产物的 actual 必须区分。

`artifact_ref` 是 host 签发的不透明句柄，带 `schema` 和 `sha256`。它在任务/归档访问域内解析；知道 ID 或 hash 不等于有读取权限。不得把 agent 输入的绝对路径、URL、`..`、软链接当作引用。源输入附件先通过 host 的导入验证，再签发 input_ref。

hash 语义：file.sha256 对文件原始字节计算；bundle 引用 sha256 对已原子发布的 manifest 原始 UTF-8 字节计算，manifest 包含所有依赖文件 hash，不包含自己的 hash。bundle 发布后不再改写。JSON 写入器固定 UTF-8、排序键、无 NaN/Infinity/重复键；下游校验实际字节，不重新序列化后代替原字节核对。

发布采用临时目录 → 校验文件 → 写 manifest → 原子登记引用。失败的目录不能被 capabilities/status 宣布为成功产物。恢复使用已登记的相同 bundle，不扫描目录猜测“可能已经完成”。

每个 file 至少有唯一 `artifact_id`、`role`、相对 `path`、`media_type`、`bytes`、`sha256`。host 逐项校验唯一 ID/路径、常规文件、限定目录、大小、内容类型与 hash；ZIP 拒绝重复项、符号链接、绝对路径/穿越、解压炸弹，限制数量、压缩比与解压总字节。schema 的路径 regex 只是第一层，不能取代真实路径检查。

## 2. ModelBundle/1

建议文件：`model.json`（规范模型）、`geometry-sim.*`、可选 `geometry-render.*`、`binding.json`、`model-report.json`、原始附件的受控 provenance 引用。旧模型首阶段可仅有参数化 model.json 与内嵌网格，不强制虚构外部网格文件。

metadata 至少定义：

| 字段 | 语义 |
| --- | --- |
| `purpose` / `domain` | physical_simulation 对应 mechanical/pcb_thermal；model_preview 对应 geometry，不创造物理状态 |
| `model_schema` | 如 `scene-v1`、带版本的 PCB spec；该 schema 的实际版本必须锁定 |
| `units` | 强类型 length/mass/time/temperature；物理长度为 m，展示默认 model_unit、质量/时间/温度为 null；显式用户尺度可用 m。旧 °C 的保留与转换记录到原配置/假设，不机械改写值 |
| `coordinates` | 右手系、Y-up、世界/局部原点；2D PCB 板面轴映射显式记录；外部 Z-up/mm 导入只做一次可审计转换 |
| `entity_ids` | 稳定且唯一；数组顺序不是身份；修订中保留未改变实体的 ID |
| `requirements` | 用户明确给定的尺寸、质量、材料、约束和事件，附来源与满足状态 |
| `assumptions` | 模板默认、推断尺寸/深度、材料估计；不得与测量值合并 |
| `simulation_asset` / `render_asset` / `raw_asset` | GeometryAsset 绑定 artifact_id、原始字节 sha256、表示类型、role、单位、坐标和 topology_sha256；物理要求 simulation/render，展示要求 raw/render 且禁止 simulation |
| `binding` | 强类型 GeometryBinding：source/target 绑定 artifact_id、sha256、topology_sha256；kind、mapping_ref、transform、error_report 均必填。物理为 identity/rigid_transform/barycentric；展示 raw→display 为 identity/exact_subset/bounded_surface |
| `geometry_quality` | reference/tolerance_m/measured_error_m/verified/limitations；请求与实际档位保留在 envelope.quality，不以面数证明容差 |
| `consumer_requirements` / `engine_dependency` | 前者列所需实体/状态/几何能力；后者为实际依赖的 DependencyPin 或 null。模拟与物理渲染必须有相同非空 engine_dependency；独立建模/展示未依赖求解器时可为空 |

high 的容差必须说明测量基准：解析曲面、用户 CAD 或采样参考。没有真值的单图重建只能声称符合已知尺寸/轮廓与假设，不能声称真实物体误差小于某数。

identity 必须有相同字节/拓扑 pin 与坐标；rigid_transform 必须是保持朝向的刚性 4×4 变换，不能藏入缩放或反射；barycentric 必须给出受验证的映射引用。exact_subset 仅证明 raw 保留坐标的精确子网格，不能许可平滑；bounded_surface 必须另外重算固定策略的删除、顶点映射、位移与保留拓扑。GeometryBinding 结构合格、pin 一致及误差值未超目标都不等于实际几何证明；后续 host 仍须读取并验证真实资产/映射/算法回执。

显示网格加密不改变物理参数和质量分布。双网格需要证明映射合法及误差；柔性形变要检查变形过程中绑定仍适用。代理无法保留显式孔洞、接触面或动力学目的时返回 `unsupported_model`，不能静默删掉。

P2 适配器负责把 bundle 投影到旧 scene/spec，并保留反向实体映射；solver 看到的规范模型也需保存。后续 CAD/图片服务输出需重新审计，不能信任供应商的“watertight/high quality”标签。

## 3. SimulationBundle/1

建议文件：`simulation.json`、`model.json` 或其受控引用、`diagnostics.json`、`queries.json`、`observations.csv`、`data-dictionary.json`、状态分块及 `state-index.json`。视频元数据不写回 SimulationBundle。

SimulationMetadata 明确 purpose=physical_simulation、model_ref、domain、engine_dependency、backend、seed、physical_interval_s、state_export、state_plan、numerical_status 和 limitations。原参数与离散方案由依赖的规范模型/求解结果保存；实际求解器 id/version/digest/interface 来自依赖锁，不能只写可变版本字符串。model_ref 必须在 inputs 中，模型用途/domain、导出 profile/时窗和引擎依赖须跨字段校验。性能估计和实际用时分开；保存的随机初态或 seed 足以解释重现范围。

### 3.1 三种保存规格

| `state_export` | 必须保存的内容 | 能证明什么 |
| --- | --- | --- |
| `observables` | 模型、诊断、预声明观测/查询、数据字典；为视频需要可另含 legacy 显示缓存 | 可查询已保存的指标；不能声称有逐粒子完整轨迹 |
| `sampled_state` | 以上内容 + 明确时间/实体选择/字段/精度的状态样本 | 在已保存采样范围内重渲染或分析；抽样覆盖必须公开 |
| `full_state` | 指定保存时刻、声明实体的全部已承诺状态字段，默认 float64，无显示舍入 | 该导出时钟上的全状态；不自动包含每个内部积分子步，也不自动是可重启 checkpoint |

full_state 是拟议能力，各 solver 必须逐个实现和验证后才能 advertised。导出 profile 独立于 visual/strict；严格观测可以只存 observables，视觉运行也可存 full_state，但后者仍没有数值精度认证。

prepare 的 `state_plan` 冻结字段、单位、dtype、实体总体、采样时钟、端点、插值方式、块大小、预计磁盘量。示例：粒子重建通常需要全体位置、粒径/核尺度、实体 ID；运动模糊如需速度则显式要求；柔体需要拓扑与每帧顶点，刚体需要位置和姿态。缺失所需字段时返回 `insufficient_render_cache`，不能靠高分辨率编码补齐。

fields 是 StateField 数组，逐字段声明 dtype/unit/components/source；entity_ids 与 population 指定总体，抽样时 selection_ref 指向不可变选择表。time_window_s、endpoints、interval_s、interpolation 和 chunking 冻结保存边界；领域验证器另外检查时间顺序、抽样表覆盖、字段与实体兼容，不能只依赖 envelope schema。

采样状态不用于补写不存在的数值观测。旧 native 显示缓存导入必须标记粒子下采样、float32/舍入和覆盖范围；不能仅改 dtype 为 float64 就标成高精数据。旧 PCB 仅部分时刻保存全场温度，min/max 的全步序列不等于每个网格点的全步历史。

### 3.2 数组与采样语义

小数据使用 JSON 与长表 CSV；大数据采用按帧分块 NPZ（禁用 object dtype/pickle），每块可独立核对 hash。`state-index.json` 对每块指定 artifact_id、array 名称、dtype、shape、unit、entity_order、time 数组和覆盖区间。初版仅固定拓扑；变化拓扑需要新的明确 encoding/capability，不把 ragged 数组塞入 object。

固定 N 粒子：`positions[T,N,3]`，可选 `velocities[T,N,3]`，`times[T]`，稳定 ID 表；柔体顶点同理且绑定 topology hash。刚体四元数约定 `wxyz`，从 local 到 world 的主动旋转；符号等价与插值方法由渲染报告声明。PCB `temperature[T,ny,nx]`，明确网格中心/边界、轴向和 K/°C 转换。

瞬态 times 必须有限、严格递增并覆盖声明区间；重复终帧、缺帧、跨块重复时间均需拒绝或显式端点去重规范。稳态使用 `mode=steady`、物理时长与采样间隔为 null，静态状态用单个 `t=0` 索引并声明它不是瞬态演化；播放时长仍可为正。保存间隔不一定等于 dt；积分时钟、观测时钟、状态输出时钟、视频时钟分别记录。资源限额不够时 prepare 拒绝；不能执行中默默改为抽样。

CSV 建议列：`run_id,query_id,entity_id,time_s,value,unit,status`。数据字典逐列定义指标、符号方向、参考系、实体归约方式及缺失语义。未观测/不支持/质量失败分别用 `not_observed/unsupported/invalid_quality`；value 为 null（CSV 空单元），不能用 0 或非标准 NaN 替代。

首版 `simulation_query` 只读取预声明且已记录的 query_id。未声明问题返回 `measurement_not_recorded`，说明缺少的量；经用户允许重新模拟后，才把查询加入新计划。未来若增加后验 QueryReport，须另行定义接收新 query spec 的受控 API、完整字段判定和验证规则，本版不隐式提供。不得以任意 eval/代码表达式执行查询。

### 3.3 质量门

`validation.integrity`、`validation.domain` 与 `validation.numerical` 分开。strict 的定量答复必须有相应 passed 证据；visual 允许列出数值警告，但不抹去原检查值。所有输出保留 `requested/actual` 品质和 scope。

完整性或硬领域门失败必须隔离，不作为成功 simulation_ref 进入正常流水线。strict 的数值门失败也不能报成功，只可明确导出无效标记的诊断。visual 若仅精度检查未过，且其余硬门通过，则可发布成功 simulation_ref 并渲染、交付带警告的数据；必须保留 `numerical=failed`，禁止有效定量答复。两次 dt 比较反映敏感性，不能未经方法论论证就称收敛证明。

## 4. RenderBundle/1

物理用途输入精确引用 ModelBundle、SimulationBundle；模型 hash 必须与模拟上游一致，除非是已经审计并引用的新展示派生资产。只读挂载 canonical 数据；渲染前后 hash 必须相同。

模型展示输入仅为 purpose=model_preview 的 ModelBundle，不允许 simulation_ref 或 SimulationBundle inputs。RenderMetadata 使用 fidelity=shape_only、simulation_performed=false、physical_interval_s=null、frame_map_ref=null；播放时间不是物理时长。raw/display 模型数据通过不需要 simulation_ref 的 DataPackageManifest 保存。

文件通常有 `simulation.mp4`、`thumbnail.png`、`render-report.json`、`frame-map.json`；深度输出可选 PNG/EXR 序列。manifest 至少有一个已验证 video role 才能算视频完成。

RenderMetadata 强类型记录 purpose、model_ref、物理用途的 simulation_ref、provider（id/version）、engine_dependency、binding、presentation（已公布模板与有界参数）、execution（renderer/version/device/seed/processing）、fidelity、physical_interval_s、playback_duration_s、分辨率/FPS/帧数、frame_map_ref、simulation_performed、measurement_source=false、reproducibility 和 limitations。物理 fidelity=trajectory_preserving|illustrative；模型展示为 shape_only。请求与实际质量在 envelope.quality，模板参数仍须按 capability 二次校验。

固定慢动作使用 `physical_time = start + playback_time * time_scale_to_physical`；视频帧的具体物理时刻保存在 frame-map，覆盖端点按编码帧中心约定说明。非线性时间映射使用显式单调表；循环、剪辑、暂停必须声明，不能省略物理片段后仍称完整时间覆盖。

标准验证包括完整解码、实际帧数/时长/FPS、实体与时间映射、代表帧（初态/关键事件/末态）、遮挡/空画面/截断、图例单位及输出字节。画面质量检查与数值质量分开；渲染器不能自行给模拟数值盖章。

外接生成式结果标记 illustrative，记录变动范围不确定；作为额外衍生视频，不能满足默认 trajectory_preserving 的 required video。艺术请求可明确选择 illustrative，原始模拟数据及其边界仍保留。

## 5. DeliveryManifest/2 与回执

delivery manifest 引用已验证输出，不承载凭证/临时签名 URL。机器字段包括 `delivery_id`、`run_id`、`required_roles`、`status`、`items`、`retention_until`；每项包括 artifact_ref、role、required、state 和 receipt_id。下载地址由 transport 在授权上下文中生成。

产物验证成功可进入 verified；排队进入 queued；transport 确认发送后进入 sent；存在可核实的平台回执时进入 acknowledged。并非每通道有已读回执，这里的 acknowledged 是平台接受证据，不能宣称用户已阅读。缺乏平台回执的交付状态必须保留其实际证据等级。

pipeline 的计算成功与 delivery 的发送成功是两个字段。用户要 video+data 时，二者验证成功后 pipeline 可 `succeeded`（计算完成）；最终对用户声称“已交付”还要 delivery 所有 required 项达到通道可证明的完成状态。required 项缺失、unknown 或发送失败不能视为完成。

数据 ZIP 内必须有不可变 `DataPackageManifest/1`（schema_version=data-package/1，仅列包内文件及其 hash，显式 purpose/model_ref；物理用途必须有一致的 simulation_ref，模型展示禁止该字段）、原模型/配置、质量报告、JSON/CSV 与字典；用户要求状态或模型时加入相应文件。包内 manifest 不含 ZIP 自己的 hash，也不含可变发送回执。外部 DeliveryManifest 引用已封存 ZIP 的 hash，回执记录在 host 数据库并输出带修订号的 delivery 快照；不得回填 ZIP 造成循环 hash。外部 provider 的临时链接不代替本地/受控持久副本。预算同时限制单文件、总交付量、工作磁盘和解压量；数据不够装时给出明确分包/存储选项，不删掉一部分后称完整。

保留窗口至少覆盖活动作业、恢复、交付重试和已告知用户的下载期限。清理先检查 DAG 引用与 receipt，后原子撤销可访问引用，再删除未被引用文件。被删除数据重渲染返回 `artifact_expired`，不能承诺从模型无成本还原。

## 6. 缓存与失效

缓存键包含模块 digest、输入 manifest hashes、规范化参数、随机种子、schema/依赖版本和相关执行配置。render key 另含相机/材质/provider 版本；硬件/非确定性影响记录重现等级。

| 变化 | 失效阶段 |
| --- | --- |
| 物理几何、材料常数、初态、边界、持续时间 | 模型适用部分、模拟及后续渲染 |
| 数值步长、观测量、保存 profile | 模拟与后续渲染；保留旧结果作为不同 run |
| 镜头、灯光、外观材质、编码 | 仅渲染与交付，前提是已存数据足够 |
| 只换传输目标/下载过期策略 | 仅交付，不重算已验证结果 |

非确定性 provider 的缓存只能复用同一实际输出，不能承诺重新提交得到相同画面。审计 provenance 保留整个依赖链，跨任务复用仍检查访问权限。
