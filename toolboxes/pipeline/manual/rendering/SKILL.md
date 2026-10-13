---
name: physics-rendering
description: 将 QQ 当前任务的完整三维展示模型渲染为 360° 相机旋转视频与模型 ZIP，或将已保存的物理模拟结果渲染为科学视频和 JSON、CSV 数据包。
---

# 模型展示、物理结果渲染与数据交付

模块内部实现、源结果校验、导出和重渲染合同见 [内部架构](architecture.md)。

本运行 Skill 的权威源在 `toolboxes/pipeline/manual/rendering/`，构建时复制进渲染模块的
`manual/`。修改源后须构建新组合，不能编辑已发布快照或运行任务的 pin。当前 v1 已支持
MP4、必需 ZIP 和独立 typed model-preview；`docs/pipeline-v2` 的异步多产物接口仍是草案。
`model-preview-result/2`、`/3` 是当前预览合同版本，不代表 pipeline-v2 已上线。

客户端另有独立的 `modeling_preview_from_image` 与 `modeling_preview_render`，供纯建模展示。
物理工具是 `pipeline_capabilities`、`pipeline_configure`、`pipeline_status`、
`pipeline_render_prepare` 和 `physics_simulate`。内部的 `render_simulation`、`export_data`
与 `stage-call/1` 由 toolbox 运行，不是客户端工具名。

新渲染 pin 声明 `portable_data=pipeline-data/2` 时，物理 ZIP 是从原私有结果
确定性派生的可移植副本，保留数值、源摘要、几何与许可证，移除本机路径并
重绑工件描述符。它不冒充原始字节或 restart checkpoint。宿主用固定 pin 的
只读验证进程从封存源独立复算，不以 ZIP 自报哈希作为充分证据。旧渲染 pin
仍使用原 `/1` 导出合同。液体表面来自记录粒子，转换回执不授权再次积分。
宿主返回不透明 `artifact_ref` 时，用它调用 `qq_video`，无需暴露本机路径。

## 360° 三维模型展示

先读当前任务的 capabilities，确认组合与宿主支持 model-preview。对已由
`modeling_preview_from_image` 成功生成的完整展示模型，调用
`modeling_preview_render`，传入其准确 `model_ref`；随后用 `qq_video` 交接返回的已验证
MP4。宿主同时验证模型 ZIP，并与所选参考图一起按 held 合同交付。不要把未经来源和
工件 pin 核验的 GLB、路径或跨事件模型当作当前模型。

`pipeline_capabilities` 描述当前组合与任务运行时，领域 capabilities 描述固定引擎；
内部 structured stage 的能力说明不覆盖全部可选图片入口。`configured` 与 advertised
availability 只表示配置/组合准入，不能替代真实推理、`ready_to_preview`、物理 prepare
或渲染验收。旧 pin 未声明的能力不能由新文档或期望的版本号补出。

该视频使用完整 display 输入网格作 360° 相机旋转；`geometry_scope=render_input`
只承诺渲染阶段不再删改该输入。声明 `bounded-floaters/1` 的建模模块默认按固定阈值清理合格的独立微小碎块，
可选 `display_cleanup=none`；原始 raw 几何与清理回执另在 ZIP 中完整保留。渲染不再
清理、减面或运行求解器。默认模型为 `model_unit`；相机居中/等比例取景只服务呈现，不恢复真实尺度，
不需要编造密度、质量或物理时长。用户另给明确尺度时保留那项假设。视频显示无贴图的
中性表面阴影，颜色只是展示样式，不能声称重建了参考图的材质或纹理。

必须通过源哈希/三角索引绑定、完整视频解码、可见表面和大小检查，再允许交付。
模型 ZIP 分别保留 raw 与 display GLB、OBJ、网格 JSON、清理/生成/渲染回执、来源与许可证；它没有
模拟数值、测量或物理轨迹。预览不能声称 `ready_to_simulate`，也不能被回答成通过
物理验收。需要物理运动或数值问题时，回到独立的建模准备和原模拟质量门。

surface 后处理须由当前 pin 明确声明 `bounded-surface/1` 并经请求选择；它可能有界移动
display 顶点。raw→display 的精确子网格或表面重算证明只覆盖展示后处理，不能证明
simulation→display 动态变形。展示 renderer 只对自身输入作相机取景，不再清理或平滑。

## 物理结果呈现

先读 `pipeline_capabilities({})`。当前本地渲染支持 `standard` 与 `preview`；深度渲染和外部商用 provider 只有 capabilities 明确声明可用时才可选择，不能把请求的深度渲染默默降成标准效果。

已有当前任务模拟结果时，读取 `pipeline_status({})`，然后调用 `pipeline_render_prepare({"rendering_quality":"standard"})`，再由 `physics_simulate` 执行已准备的渲染任务。此路径复用保存的结果，不改变模型、不重复求解。缺少保存结果时应返回需要先完成模拟，不能凭空渲染出“模拟证据”。首次模拟可用 `pipeline_configure({"modeling_quality":"standard","simulation_quality":"inherit","rendering_quality":"standard"})` 选择渲染档位，再走现有建模和模拟流程。`simulation_quality` 的 `visual`、`strict` 与渲染档位独立。

默认交付 `simulation.mp4` 和 `data.zip`。视频使用录制状态渲染：机械模型保留科学视图、自动取景，展示时长在 3–30 秒之间；短过程慢放、长过程加速时覆盖完整的保存时间区间，回执标明物理时长和播放倍率。PCB 用固定数值色标显示板面温度和耗散功率。视频中的插值只改变展示。数据包按固定 pin 的合同提供结果、模型、已有测量、CSV、质量状态、数据字典及文件摘要：旧 `/1` 保留源 `result.json` 与模型 bundle 的原始字节；新 `/2` 提供可移植派生副本，保留数值和原始源摘要，由固定包的只读进程独立复算验证。新 `/2` 的导出字节不冒充原私有结果字节。

物理网格视频绘制 result 中与准备场景绑定的模拟顶点和三角拓扑。物理路线保留的详细
display GLB/OBJ/JSON 是来源和导出资产；当前没有一般高模随低模运动的变形绑定，不能
承诺它们自动替代模拟网格出现在动态视频中。粒子液体的连续液面来自录制粒子的屏幕
空间视觉重建，不修改粒子状态/碰撞半径，不是测得体积、光学解或可交给求解器的新网格。

回答数据问题时使用数据包的测量或已声明的 `physics_query`。`display-frames.csv` 是经过舍入/抽样的显示状态，不能补成未保存的全量求解观测。没有预声明测量时，说明这次数据包可提供的字段；新的高精度观测需要在新模拟前声明。PCB CSV 的坐标是米，温度是摄氏度，功率是每个网格单元的瓦数，行号从板底边开始。

读取 `quality.json` 或阶段回执中的质量状态：`quantitative_usable=false` 时可以交付带标签的诊断数据和视频，但不能把数字解释成通过数值验收的结论。画面更清晰、慢放、后期美化均不提升模拟精度。PCB 是厚度平均的板级模型；网格加密不自动补齐封装、铜迹、过孔或厚度方向热点物理。

QQ 视频和 ZIP 分别跟踪投递回执。只按实际回执声称“已发送”；部分投递失败时报告各产物的 sent、failed 或 unknown 状态。unknown 不自动重发，也不计为成功；仅在宿主提供明确恢复路径时才复用核验后的原产物。数据包超出文件大小限制时返回明确原因，不能默默丢字段或抽样后仍声称完整数据。

呈现失败先读 `pipeline_status`。成功模拟可在同一任务、原组件 pin 和哈希检查下重新
prepare render-only；不得因此原样重跑失败物理、修改模型或删除不完整阶段目录。
跨任务导入和 renderer 版本迁移不属于当前恢复接口。

未来的 diffusion 视频是另一个带 `illustrative_only` 标签的演示产物，数值解释仍指向科学视频和数据包。外部 adapter 需明确上传范围和费用上限；当前声明式 provider 契约不会发网络请求，也不意味着服务已经连接。
