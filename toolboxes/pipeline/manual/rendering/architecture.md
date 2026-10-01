# 渲染模块内部架构

渲染模块有独立模型展示与物理结果呈现两条路径。前者从当前任务绑定的完整 display 网格绘制 360° 相机旋转，导出模型 ZIP；后者从保存且经过验证的模拟结果生成科学视频和完整数值数据包。模块不运行求解器、不改变模型与原始结果、不把旋转或插值画面当成测量。物理呈现使用 local-scientific provider；质量档位 preview/standard 与独立 model-preview 目的分开。deep/PBR/diffusion 仍需能力发现与独立实现。操作说明见 [Skill](SKILL.md)，恢复与 pin 见 [组合架构](../../ARCHITECTURE.md)。

```text
当前事件 model_ref + 完整 display mesh / GLB / OBJ / 生成回执
  → current image/runtime/modeling/rendering pins 与源哈希核验
  → model_preview：全部 display 输入三角面 → 等比例相机取景 → 360° 旋转 H.264
  → 全帧解码/帧数/时长/可见表面检查 + source unchanged
  → 完整模型 ZIP + model-preview 结果合同 → 宿主检查及 qq_video 交付
    （simulation 跳过；model_unit；没有物理测量）

保存 result + model + simulation manifest
  → artifacts.load_source：模型绑定、领域质量门、全部声明摘要
  → export_data：完整原始 JSON + CSV + 字典 + provenance → data.zip
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

渲染保留全部输入 display 顶点、三角索引与断开的组件。0.3.1 的 `geometry_scope=render_input`
和 `input_geometry_retained=true` 限定该承诺，不宣称建模阶段未清理生成碎块。raw 几何
与 fixed-policy cleanup receipt 另在 v2 ZIP/provenance 中绑定。有限坐标、索引、空间范围
和独立预算仍严格准入；相机归一化只在呈现副本内等比例居中/取景。渲染没有额外组件
删除、减面、碰撞、积分器或模拟时间轴。默认 `model_unit` 不解释为米，显式
用户尺度另有来源；质量、密度、材料和测量均不由此估计。

本机固定 native triangle drawer 绘制中性无贴图表面，相机绕模型完整旋转。源三角
索引摘要与实际传给绘制器的摘要一致；可见表面记录逐帧检查。固定 ffmpeg 编码 H.264，
随后 ffprobe 检查帧数、分辨率和播放时长，并完整解码视频。标签明确 AI MODEL PREVIEW、
SHAPE ONLY 与 no simulation or measurements。颜色属于呈现样式，不是纹理生成。

输出 `model-preview.mp4`、海报和 `model-preview-render/1` 回执；组合导出完整 GLB、OBJ、
raw/display mesh JSON、清理/生成/渲染回执、来源与许可，逐文件绑定哈希并验证 ZIP。最终合同标记
`result_kind=model-preview`、`simulation_performed=false`、`measurement_source=false`，
宿主核验后由 qq_video 交付 MP4 和模型 ZIP。没有 `pipeline-simulation/1` 或假造数值数据。

模型可展示不证明可模拟；原物理 gate 保持。生成或渲染失败必须具体报告，不能将空白视频、
截取参考图、单张图片平移或未知工件当作三维模型旋转展示。完整原始网格和来源工件在
发布前复核不变；预览不改写原模拟 checkpoint。新 0.3.1 的真实新图完整验收由最终记录
另行确认，不以实现完成、构建或 health 代替实际工件证据。

## 输入身份与数值质量

机械 result.scene 的 canonical 内容必须与 supplied normalized model 相同。持久结果通过引擎 results 检查；如果有预声明 queries，measurements 还需独立重新验证及与保存观测绑定。没有 measurements 时不能从视频或显示帧补造。

存在 `simulation-manifest.json` 时，渲染验证 schema/domain、quality/data 布尔值、model/result 引用和全部声明 artifacts。上游 stage 的失败或 data_usable=false 必须降低下游 numerical 可用性；不能因为原 result 的内部质量门通过，就提升已失败 stage 为成功。

PCB 则按固定模型重新检查尺寸、网格、时间、功率、有限温度和能量残差。两条路径均区分：

- 交付/视觉门是否通过；
- numerical_passed 与 quantitative_usable 是否允许定量结论；
- 宏步测量、时间序列和采样显示状态各自的精度/时钟。

画面更清晰、慢放、插值、压缩或未来美化均不提高物理精度。数值门未通过时只发布带 diagnostic 标签的数据，不隐藏 precision warnings 或添加认证声明。

## 完整、确定性的数据导出

`export_data` 的源是原结果的实际 bytes。ZIP 保留原 `result.json` 和 model bundle，并增加 normalized model、measurements、quality、data dictionary、采样说明及 SHA-256 manifest；有 stage 证据时，复制全部声明的 stage 工件到 source-stage。

| 领域 | CSV 数据 |
|---|---|
| physics | observations：预声明宏步测量；display-frames：明确不可定量使用的显示状态 |
| pcb_thermal | 保存 temperature snapshots 的 cell center 网格；完成热时间步的 scalar extrema |

CSV 标识符安全转义公式前缀，数字符号保持。单位、坐标原点、row 顺序、质量状态和 source 列进入数据字典。未预声明 measurements 时，数据包写 unavailable 原因，仍保留实际已有数据。

ZIP 用排序文件名、固定时间戳、Unix regular-file 权限和 ZIP_STORED，避免压缩库版本造成输出变化。manifest 逐成员记录 bytes/hash，再生成 envelope 和 `data-manifest.json`。输出既检查展开字节预算，也检查完整 ZIP 最终字节预算；超限返回 `export_too_large`，不会抽样或删字段后声称完整。

发布前 `verify_unchanged` 再计算 result/model/measurements/stage 所有已追踪源哈希。render/export 可以替换自己的呈现产物，但没有权限修改上游数值证据。

## 科学视频生成

mechanical 视频使用固定引擎的录制状态绘制与自动取景。正物理时长映射为 3–30 秒播放时间，覆盖整段保存时间区间；preview 用 15 fps，standard 用 30 fps。显示插值和旋转插值来自录制状态，不再积分。零物理时长使用有界静态帧编码。

机械中间 MP4 保持独立大小上限，最终 H.264 用单线程 software libx264、yuv420p、faststart、移除源 metadata。CRF 20/27/34 逐级尝试满足宿主大小预算，preview 另缩放宽度至 640。每次成功候选执行完整解码，再核对帧数与中间视频一致、播放时长偏差不超过 0.05 秒。源音轨不交付。

PCB 视频使用固定温度色标、板面温度与耗散功率；显示时间可插值，但原数值时刻、采样和质量保持独立。呈现回执保存 physical_duration、playback、interpolation、media sample count 和 source hash。

render deadline 只消耗宿主给予的剩余时间；null 保持无限时钟语义。字节上限始终存在。deep 未配置、ffmpeg 缺失、超时、不能满足大小或解码/帧数不一致均显式失败，不交付未经检查的视频。

## 产物、重渲染与外部 provider

模块发布 `simulation.mp4`、`render-manifest.json`、`data.zip`、`data-manifest.json`。render receipt 包含源 result/model 摘要、provider、quality、solver_rerun=false、measurement_source=false、numerical_usable、sampling、warnings、presentation、codec/decode 及产物哈希。

当前任务的成功模拟 checkpoint 经组合验证后，可 `pipeline_render_prepare` → 原 execution 入口走 render_only。它只重新导出/渲染，保持模拟 bytes 和 solver invocation 次数。源结果变化、失效 checkpoint 或跨任务导入被拒绝；修改物理模型则需新 prepare 和数值运行。

外部 provider 接入未来需要明确数据上传范围、授权、费用、网络与产物合同。声明式 provider 请求/回执验证只证明来源关联，不代表网络服务启用或产物数值可信。生成艺术演示必须另标 illustrative_only，不能替换科学结果或取消质量门。

宿主分别跟踪 MP4 与 ZIP 的平台回执；模块只准备产物，不能把本机生成完成写成 QQ 已送达，unknown 不重发。

## 回归与扩展

`toolboxes/tests/test_pipeline_presentation.py` 验证 ZIP 确定性、完整 CSV、诊断质量、上游 stage 降级、路径/摘要篡改、绑定模型、未声明测量、预算、deep 拒绝及真实解码重渲染不调用求解器。组合测试验证导出/视频失败保留成功模拟和 render_only 恢复；根渲染测试验证几何、液面、姿态、自动取景和解码。

新 renderer 必须保存源绑定和完整时间范围、给出插值与生成标签，并在真正的 codec/decode/大小回归通过后宣布可用。数值质量、原始数据、完整输出与真实回执不因 renderer 更换而降低。
