---
name: physics-rendering
description: 将 QQ 当前任务的完整三维展示模型渲染为 360° 相机旋转视频与模型 ZIP，或将已保存的物理模拟结果渲染为科学视频和 JSON、CSV 数据包。
---

# 模型展示、物理结果渲染与数据交付

模块内部实现、源结果校验、导出和重渲染合同见 [内部架构](architecture.md)。

客户端另有独立的 `modeling_preview_from_image` 与 `modeling_preview_render`，供纯建模展示。
物理工具是 `pipeline_capabilities`、`pipeline_configure`、`pipeline_status`、
`pipeline_render_prepare` 和 `physics_simulate`。内部的 `render_simulation`、`export_data`
与 `stage-call/1` 由 toolbox 运行，不是客户端工具名。

## 360° 三维模型展示

先读当前任务的 capabilities，确认组合与宿主支持 model-preview。对已由
`modeling_preview_from_image` 成功生成的完整展示模型，调用
`modeling_preview_render`，传入其准确 `model_ref`；随后用 `qq_video` 交接返回的已验证
MP4。宿主同时验证模型 ZIP，并与所选参考图一起按 held 合同交付。不要把未经来源和
工件 pin 核验的 GLB、路径或跨事件模型当作当前模型。

该视频使用完整源网格作 360° 相机旋转，保留全部连通组件，无模拟微屑清理、减面或
求解器。默认模型为 `model_unit`；相机居中/等比例取景只服务呈现，不恢复真实尺度，
不需要编造密度、质量或物理时长。用户另给明确尺度时保留那项假设。视频显示无贴图的
中性表面阴影，颜色只是展示样式，不能声称重建了参考图的材质或纹理。

必须通过源哈希/三角索引绑定、完整视频解码、可见表面和大小检查，再允许交付。
模型 ZIP 保留完整 GLB、OBJ、网格 JSON、生成/渲染回执、来源与许可证；它没有
模拟数值、测量或物理轨迹。预览不能声称 `ready_to_simulate`，也不能被回答成通过
物理验收。需要物理运动或数值问题时，回到独立的建模准备和原模拟质量门。

## 物理结果呈现

先读 `pipeline_capabilities({})`。当前本地渲染支持 `standard` 与 `preview`；深度渲染和外部商用 provider 只有 capabilities 明确声明可用时才可选择，不能把请求的深度渲染默默降成标准效果。

已有当前任务模拟结果时，读取 `pipeline_status({})`，然后调用 `pipeline_render_prepare({"rendering_quality":"standard"})`，再由 `physics_simulate` 执行已准备的渲染任务。此路径复用保存的结果，不改变模型、不重复求解。缺少保存结果时应返回需要先完成模拟，不能凭空渲染出“模拟证据”。首次模拟可用 `pipeline_configure({"modeling_quality":"standard","simulation_quality":"inherit","rendering_quality":"standard"})` 选择渲染档位，再走现有建模和模拟流程。`simulation_quality` 的 `visual`、`strict` 与渲染档位独立。

默认交付 `simulation.mp4` 和 `data.zip`。视频使用录制状态渲染：机械模型保留科学视图、自动取景，展示时长在 3–30 秒之间；短过程慢放、长过程加速时覆盖完整的保存时间区间，回执标明物理时长和播放倍率。PCB 用固定数值色标显示板面温度和耗散功率。视频中的插值只改变展示。数据包包含原始 `result.json`、模型 bundle、标准化模型、已有测量、CSV、质量状态、数据字典及文件摘要。

回答数据问题时使用数据包的测量或已声明的 `physics_query`。`display-frames.csv` 是经过舍入/抽样的显示状态，不能补成未保存的全量求解观测。没有预声明测量时，说明这次数据包可提供的字段；新的高精度观测需要在新模拟前声明。PCB CSV 的坐标是米，温度是摄氏度，功率是每个网格单元的瓦数，行号从板底边开始。

读取 `quality.json` 或阶段回执中的质量状态：`quantitative_usable=false` 时可以交付带标签的诊断数据和视频，但不能把数字解释成通过数值验收的结论。画面更清晰、慢放、后期美化均不提升模拟精度。PCB 是厚度平均的板级模型；网格加密不自动补齐封装、铜迹、过孔或厚度方向热点物理。

QQ 视频和 ZIP 分别跟踪投递回执。只按实际回执声称“已发送”；部分投递失败时报告已送达的产物和未送达的产物，复用原产物重试。数据包超出文件大小限制时返回明确原因，不能默默丢字段或抽样后仍声称完整数据。

未来的 diffusion 视频是另一个带 `illustrative_only` 标签的演示产物，数值解释仍指向科学视频和数据包。外部 adapter 需明确上传范围和费用上限；当前声明式 provider 契约不会发网络请求，也不意味着服务已经连接。
