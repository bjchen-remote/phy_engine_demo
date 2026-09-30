---
name: physics-rendering
description: 将 QQ 当前任务已保存的物理模拟结果渲染成视频，并交付可提取的 JSON、CSV 数据包；适用于重渲染、预览与视频和数据交付。
---

# 物理结果渲染与数据交付

模块内部实现、源结果校验、导出和重渲染合同见 [内部架构](architecture.md)。

客户端工具是 `pipeline_capabilities`、`pipeline_configure`、`pipeline_status`、`pipeline_render_prepare` 和 `physics_simulate`。内部的 `render_simulation`、`export_data` 与 `stage-call/1` 由 toolbox 运行，不是客户端工具名。

先读 `pipeline_capabilities({})`。当前本地渲染支持 `standard` 与 `preview`；深度渲染和外部商用 provider 只有 capabilities 明确声明可用时才可选择，不能把请求的深度渲染默默降成标准效果。

已有当前任务模拟结果时，读取 `pipeline_status({})`，然后调用 `pipeline_render_prepare({"rendering_quality":"standard"})`，再由 `physics_simulate` 执行已准备的渲染任务。此路径复用保存的结果，不改变模型、不重复求解。缺少保存结果时应返回需要先完成模拟，不能凭空渲染出“模拟证据”。首次模拟可用 `pipeline_configure({"modeling_quality":"standard","simulation_quality":"inherit","rendering_quality":"standard"})` 选择渲染档位，再走现有建模和模拟流程。`simulation_quality` 的 `visual`、`strict` 与渲染档位独立。

默认交付 `simulation.mp4` 和 `data.zip`。视频使用录制状态渲染：机械模型保留科学视图、自动取景，展示时长在 3–30 秒之间；短过程慢放、长过程加速时覆盖完整的保存时间区间，回执标明物理时长和播放倍率。PCB 用固定数值色标显示板面温度和耗散功率。视频中的插值只改变展示。数据包包含原始 `result.json`、模型 bundle、标准化模型、已有测量、CSV、质量状态、数据字典及文件摘要。

回答数据问题时使用数据包的测量或已声明的 `physics_query`。`display-frames.csv` 是经过舍入/抽样的显示状态，不能补成未保存的全量求解观测。没有预声明测量时，说明这次数据包可提供的字段；新的高精度观测需要在新模拟前声明。PCB CSV 的坐标是米，温度是摄氏度，功率是每个网格单元的瓦数，行号从板底边开始。

读取 `quality.json` 或阶段回执中的质量状态：`quantitative_usable=false` 时可以交付带标签的诊断数据和视频，但不能把数字解释成通过数值验收的结论。画面更清晰、慢放、后期美化均不提升模拟精度。PCB 是厚度平均的板级模型；网格加密不自动补齐封装、铜迹、过孔或厚度方向热点物理。

QQ 视频和 ZIP 分别跟踪投递回执。只按实际回执声称“已发送”；部分投递失败时报告已送达的产物和未送达的产物，复用原产物重试。数据包超出文件大小限制时返回明确原因，不能默默丢字段或抽样后仍声称完整数据。

未来的 diffusion 视频是另一个带 `illustrative_only` 标签的演示产物，数值解释仍指向科学视频和数据包。外部 adapter 需明确上传范围和费用上限；当前声明式 provider 契约不会发网络请求，也不意味着服务已经连接。
