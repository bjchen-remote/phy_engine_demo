# 渲染模块内部架构

渲染模块有独立模型展示与物理结果呈现两条路径。前者从当前任务绑定的完整 display 网格绘制 360° 相机旋转，由组合层导出模型 ZIP；后者从保存且经过验证的模拟结果生成科学视频和完整数值数据包。模块不运行求解器、不改变模型与原始结果、不把旋转或插值画面当成测量。物理呈现使用 local-scientific provider；质量档位 preview/standard 与独立 model-preview 目的分开。deep/PBR/diffusion 仍需能力发现与独立实现。操作说明见相邻 [Skill](SKILL.md)；恢复与 pin 的源码说明位于 `toolboxes/pipeline/ARCHITECTURE.md`。

本文与 Skill 的源目录为 `toolboxes/pipeline/manual/rendering/`，构建时随
`pipeline_presentation/` 复制到 rendering 模块，手册位于模块的 `manual/`；固定
worker、module/toolbox 元数据由构建器生成。手册属于模块摘要，改源后构建新组合，
不编辑生成包或旧 pin。当前 v1 已支持 MP4+ZIP 与 typed model-preview；
`docs/pipeline-v2` 的异步多产物协议仍是草案。preview `/2`、`/3` schema 不是该草案的
上线证据。组合能力来自验证后的 module 声明和任务 runtime pin，configured 或可用
声明不能代替实际推理、prepare、渲染验收和平台回执。

```text
当前事件 model_ref + 完整 display mesh / GLB / OBJ / 生成回执
  → current image/runtime/modeling/rendering pins 与源哈希核验
  → model_preview：全部 display 输入三角面 → 等比例相机取景 → 360° 旋转 H.264
  → 全帧解码/帧数/时长/可见表面检查 + source unchanged
  → 完整模型 ZIP + model-preview 结果合同 → 宿主检查及 qq_video 交付
    （simulation 跳过；model_unit；没有物理测量）

保存 result + model + simulation manifest
  → artifacts.load_source：模型绑定、领域质量门、全部声明摘要
  → export_data：按 pin 导出 /1 原字节或 /2 可移植派生 JSON + CSV + 字典 + provenance → data.zip
  → render_simulation：保存状态绘制 → H.264 → 全帧解码/字节验证
  → verify_unchanged：源文件不变
  → presentation manifests → 组合校验 → 宿主检查及交付
```

## 实现分层

| 实现 | 责任 |
|---|---|
| `pipeline/worker.py` | rendering role 接受固定 export / render / model_preview action，目的明确分开 |
| `pipeline_presentation/model_preview.py` | 当前完整三角网格的本机中性表面绘制、360° 相机旋转、视频/可见性验证与源不变回执；不调用求解器 |
| `pipeline/model_preview.py` | model_ref 和来源/pin 核验、固定 rendering 阶段调用、完整模型 ZIP 和 model-preview 宿主合同 |
| `pipeline_presentation/artifacts.py` | saved source、模型/阶段证据、质量标签、CSV、完整数据字典、确定性 ZIP 和原子发布 |
| `pipeline_presentation/rendering.py` | 保存状态渲染、独立时间/视频大小预算、H.264 转码和解码、render receipt |
| `pipeline_presentation/providers.py` | 外部 provider 的离线 request/receipt 合同；不调用网络或声称外部服务已接通 |
| 固定引擎 `physics_demo.io.video` / `pcb_video` | 科学几何/温度绘制、录制状态插值、自动取景和完整视频解码 |
| 组合 `adapter.py` | 原模拟 checkpoint、先 export 再 render、render_only 与最终 v1 result-manifest |

所有结果和文件来自当前任务与固定组件组合。`load_source` 只读取 bounded regular JSON，拒绝软链接、重复键、非有限数字和越界 artifact 引用。它记录源文件的实际字节摘要，以便发布前重新校验。

## 独立模型展示合同

`modeling_preview_render` 只接受当前事件 `model_ref` 与有界相机参数。它重新验证成功
display-only 生成回执、来源图片摘要、全部展示工件和 rendering module pin，按模型/
参数/模块摘要分配独立输出；不能导入旧物理失败任务、任意路径或跨事件模型。

渲染保留全部输入 display 顶点、三角索引与断开的组件。清理组合使用 `geometry_scope=render_input`
和 `input_geometry_retained=true` 限定该承诺，不宣称建模阶段未清理生成碎块。raw 几何
与 fixed-policy cleanup receipt 另在 preview `/2` 或 surface `/3` ZIP/provenance 中绑定。
surface 的坐标变化在建模后处理发生，renderer 不再平滑或删面；v3 宿主从 raw 精确
重算有界删除、映射、位移和保留拓扑。完整 display 输入保留不等于 raw/display 坐标不变。
有限坐标、索引、空间范围
和独立预算仍严格准入；相机归一化只在呈现副本内等比例居中/取景。渲染没有额外组件
删除、减面、碰撞、积分器或模拟时间轴。默认 `model_unit` 不解释为米，显式
用户尺度另有来源；质量、密度、材料和测量均不由此估计。

本机固定 native triangle drawer 绘制中性无贴图表面，相机绕模型完整旋转。源三角
索引摘要与实际传给绘制器的摘要一致；可见表面记录逐帧检查。固定 ffmpeg 编码 H.264，
随后 ffprobe 检查帧数、分辨率和播放时长，并完整解码视频。标签明确 AI MODEL PREVIEW、
SHAPE ONLY 与 no simulation or measurements。颜色属于呈现样式，不是纹理生成。

输出 `model-preview.mp4`、海报和 `model-preview-render/1` 回执；组合导出完整 GLB、OBJ、
raw/display mesh JSON、清理/生成/渲染回执、来源与许可，逐文件绑定哈希并验证 ZIP。最终合同标记
`result_kind=model-preview`，verification 中 `simulation_performed=false`、`numerical_passed=false`；
其绑定的 render receipt 另有 `measurement_source=false`。宿主核验后由 qq_video 交付
MP4 和模型 ZIP。没有 `pipeline-simulation/1` 或假造数值数据。

模型可展示不证明可模拟；原物理 gate 保持。生成或渲染失败必须具体报告，不能将空白视频、
截取参考图、单张图片平移或未知工件当作三维模型旋转展示。完整原始网格和来源工件在
发布前复核不变；预览不改写原模拟 checkpoint。真实新图完整验收、安装副本和平台
送达分别记录，不以实现完成、构建或 health 代替实际工件证据。

## 几何角色与映射边界

| 呈现输入 | 当前绑定 | 不可推出的能力 |
|---|---|---|
| preview display mesh | raw→display 的精确子集或 bounded-surface 重算；renderer 对完整 display 作等比取景 | 闭合实体、材料/质量测量、物理运动 |
| physical mesh trajectory | `mesh_objects` 的 triangles/顶点区间与准备 scene 一致，逐帧绘制 `frames[*].m` | 详细 display mesh 随简化 simulation mesh 动态变形 |
| liquid recorded particles | 录制位置/材料标签驱动屏幕空间连续表面与阴影 | 新的物理三角网格、测得体积、光学求解或更高物理精度 |
| rerender | 原 result/model/checkpoint 与原 pin，显示插值和编码 | 新的求解状态、任意未声明测量、跨任务或版本迁移 |

模型生成的均匀缩放、候选减面后的尺度恢复和 preview 相机 normalization 都有各自
回执。它们说明静态坐标关系，不是两套不同拓扑之间的 cage、蒙皮或重心坐标绑定。
物理路线保存的详细 display GLB/OBJ/JSON 进入 provenance/ZIP；当前科学视频仍绘制
模拟 mesh。raw→display 后处理映射不能替代 simulation→display 逐帧绑定。

## 输入身份与数值质量

机械 result.scene 的 canonical 内容必须与 supplied normalized model 相同。持久结果通过引擎 results 检查；如果有预声明 queries，measurements 还需独立重新验证及与保存观测绑定。没有 measurements 时不能从视频或显示帧补造。

存在 `simulation-manifest.json` 时，渲染验证 schema/domain、quality/data 布尔值、model/result 引用和全部声明 artifacts。上游 stage 的失败或 data_usable=false 必须降低下游 numerical 可用性；不能因为原 result 的内部质量门通过，就提升已失败 stage 为成功。

PCB 则按固定模型重新检查尺寸、网格、时间、功率、有限温度和能量残差。两条路径均区分：

- 交付/视觉门是否通过；
- numerical_passed 与 quantitative_usable 是否允许定量结论；
- 宏步测量、时间序列和采样显示状态各自的精度/时钟。

画面更清晰、慢放、插值、压缩或未来美化均不提高物理精度。数值门未通过时只发布带 diagnostic 标签的数据，不隐藏 precision warnings 或添加认证声明。

## 完整、确定性的数据导出

`export_data` 始终读取封存源结果的实际 bytes，不改写私有原文件。旧渲染 pin 的 `/1` ZIP 保留原 `result.json`、model bundle 和已声明 stage 工件的字节，并增加 normalized model、measurements、quality、data dictionary、采样说明及 SHA-256 manifest。声明 `portable_data=pipeline-data/2` 的新渲染 pin 则导出可移植派生副本：保留数值和原始源摘要，移除本机路径并重新绑定内部工件描述符，source-stage 证据也按该合同投影。它不声称派生 JSON 与私有源文件字节相同，也不是 restart checkpoint。宿主调用固定包的只读验证进程，从封存源和清单独立复算完整 ZIP；ZIP 自报的成员摘要不足以证明派生正确。

| 领域 | CSV 数据 |
|---|---|
| physics | observations：预声明宏步测量；display-frames：明确不可定量使用的显示状态 |
| pcb_thermal | 保存 temperature snapshots 的 cell center 网格；完成热时间步的 scalar extrema |

CSV 标识符安全转义公式前缀，数字符号保持。单位、坐标原点、row 顺序、质量状态和 source 列进入数据字典。未预声明 measurements 时，数据包写 unavailable 原因，仍保留实际已有数据。

ZIP 用排序文件名、固定时间戳和 Unix regular-file 权限；科学数据包先使用 ZIP_STORED，
超过最终字节预算时可改用无损 DEFLATE，保留所选 `/1` 或 `/2` 合同生成的每个成员及其 bytes/hash；
`/2` 的成员摘要对应派生字节，原私有文件摘要另行绑定。压缩封装
字节不代替成员完整性证明；不能承诺跨压缩库版本的压缩 ZIP 字节一致。manifest 逐成员
记录 bytes/hash，再生成 envelope 和 `data-manifest.json`。展开字节和最终 ZIP 字节
分别受限；仍超限则返回 `export_too_large`，不会抽样或删字段后声称完整。

发布前 `verify_unchanged` 再计算 result/model/measurements/stage 所有已追踪源哈希。render/export 可以替换自己的呈现产物，但没有权限修改上游数值证据。

## 科学视频生成

mechanical 视频使用固定引擎的录制状态绘制与自动取景。正物理时长映射为 3–30 秒播放时间，覆盖整段保存时间区间；preview 用 15 fps，standard 用 30 fps。显示插值和旋转插值来自录制状态，不再积分。零物理时长使用有界静态帧编码。

机械中间 MP4 保持独立大小上限，最终 H.264 用单线程 software libx264、yuv420p、faststart、移除源 metadata。CRF 20/27/34 逐级尝试满足宿主大小预算，preview 另缩放宽度至 640。每次成功候选执行完整解码，再核对帧数与中间视频一致、播放时长偏差不超过 0.05 秒。源音轨不交付。

PCB 视频使用固定温度色标、板面温度与耗散功率；显示时间可插值，但原数值时刻、采样和质量保持独立。呈现回执保存 physical_duration、playback、interpolation、media sample count 和 source hash。

render deadline 只消耗宿主给予的剩余时间；null 保持无限时钟语义。字节上限始终存在。deep 未配置、ffmpeg 缺失、超时、不能满足大小或解码/帧数不一致均显式失败，不交付未经检查的视频。

## 产物、重渲染与外部 provider

模块发布 `simulation.mp4`、`render-manifest.json`、`data.zip`、`data-manifest.json`。render receipt 包含源 result/model 摘要、provider、quality、solver_rerun=false、measurement_source=false、numerical_usable、sampling、warnings、presentation、codec/decode 及产物哈希。

当前任务的成功模拟 checkpoint 经组合验证后，可 `pipeline_render_prepare` → 原 execution 入口走 render_only。它只重新导出/渲染，保持模拟 bytes 和 solver invocation 次数。源结果变化、失效 checkpoint 或跨任务导入被拒绝；修改物理模型则需新 prepare 和数值运行。

该恢复保留原组件 pin；不会自动把旧结果迁移给新 renderer。失败的物理模型不能原样
重试，不完整阶段目录保留证据而不覆盖。已验证的成功模拟被复用与重跑失败数值任务
是不同操作，呈现失败不能成为重复求解或缩短请求物理时长的理由。

外部 provider 接入未来需要明确数据上传范围、授权、费用、网络与产物合同。声明式 provider 请求/回执验证只证明来源关联，不代表网络服务启用或产物数值可信。生成艺术演示必须另标 illustrative_only，不能替换科学结果或取消质量门。

宿主分别跟踪 MP4 与 ZIP 的平台回执；模块只准备产物，不能把本机生成完成写成 QQ 已送达，unknown 不重发。

## 回归与扩展

`toolboxes/tests/test_pipeline_presentation.py` 验证 ZIP 确定性、完整 CSV、诊断质量、上游 stage 降级、路径/摘要篡改、绑定模型、未声明测量、预算、deep 拒绝及真实解码重渲染不调用求解器。组合测试验证导出/视频失败保留成功模拟和 render_only 恢复；根渲染测试验证几何、液面、姿态、自动取景和解码。

新 renderer 必须保存源绑定和完整时间范围、给出插值与生成标签，并在真正的 codec/decode/大小回归通过后宣布可用。数值质量、原始数据、完整输出与真实回执不因 renderer 更换而降低。
