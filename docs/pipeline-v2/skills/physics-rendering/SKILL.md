---
name: physics-rendering
description: 渲染技能设计草案：将已保存的模拟和模型资产制作成可验证的视频与展示产物，支持独立质量及受控外接渲染；不修改模拟数据或生成数值证据。
---

# 物理渲染

> **设计草案，尚未注册或实现。** 本文件不启用任何渲染 API；不得在现有 v1 客户端调用下述新操作。只调用宿主实际公布且可用的能力。

本技能按 purpose 消费不可变引用并产出 RenderBundle：physical_simulation 读取 SimulationBundle 和其精确模型依赖；model_preview 只读取模型的完整 display 资产，无模拟/测量/物理时间。签名见 [API 契约](../../api-contract.md)，时间与产物字段见 [产物契约](../../artifact-contract.md)，交付流程见 [规划书](../../README.md)。

## 输入与输出

- 输入：用途、model_ref 和有界展示设置；物理用途另必须给 simulation_ref，其上游 model_ref/hash 和 engine_dependency 须一致。展示用途禁止 simulation_ref。
- 输出：强类型 RenderMetadata、GeometryBinding、视频/报告与 RenderBundle 引用。物理用途有 frame_map/物理时间映射和独立模拟数据；展示为 fidelity=shape_only、simulation_performed=false、物理时间/frame_map 为 null，另交 raw/display 模型数据。
- 渲染质量为 `preview|standard|deep`，默认 `standard`；`deep` 改善视觉质量，不增加物理精度，也不使失败的数值门禁通过。

## 执行流程

1. 弱客户端 agent 优先使用宿主 `pipeline_prepare → pipeline_submit → pipeline_status → pipeline_deliver`，取消使用 `pipeline_cancel`。默认建模 `standard`、模拟 `visual`、渲染 `standard`，交付视频和数据。
2. 单独渲染时调用 rendering_capabilities，核对用途、GeometryBinding、provider 与输出能力。沿用 rendering_prepare：显式传 purpose/model_ref/spec/quality；仅 physical_simulation 传 simulation_ref，model_preview 不传该字段。两条路径都不创建新工具名。
3. 检查返回的 plan_id、用途、相关组件/依赖 pin、资源/费用、provider 策略和拟执行质量。物理用途另核对完整物理时间范围与播放映射；展示的 state_plan 必须为 null。仅有选择深度渲染的意图不代表允许任何外部上传或无上限费用。
4. 用该 `plan_id` 和宿主提供的 `idempotency_key` 调用 `rendering_render`。外接 provider 由宿主管理凭据、提交、轮询与下载；客户端不拼接 URL、路径或外部命令。
5. render 返回 `job_id` 后，按 `next_action.poll_after_ms` 调用 `pipeline_status`，取得成功的 `render_ref` 后再调用 `rendering_inspect`。核验输入 hash、帧覆盖、时间映射、解码、大小和实际质量，通过后由 `pipeline_deliver` 按该作业的交付计划发布。

## 关键决策

- 深度渲染默认不启用外接 provider；只有已公布能力且宿主现有策略允许时采用。已有授权充分时继续执行，不统一要求用户重新确认；策略缺失时返回可解释阻塞或符合要求的本地选项。
- 重渲染直接使用已有模拟数据。调整镜头、灯光、材质或编码不能更改求解器状态、模型物理参数、测量值或 canonical 数据 hash。
- “昨天/上次的结果”只使用 host 在上下文中提供的已授权历史引用；缺失或有歧义时补充选择，不能猜 ID。用户禁止重算时冻结 `permitted_stages`，不得执行 modeling/simulation；缺缓存就报告边界。
- 平滑、插帧、慢动作属于展示处理；记录物理时长、播放时长和映射。插值帧不是新增物理采样，不能用其作测量依据。
- 显示网格与模拟网格不同则验证 source/target 的 artifact/hash/topology、映射及误差；展示 raw→display 的 exact_subset 不许可移动坐标，bounded_surface 需完整算法复核。仅通过 schema 或回执自报不算几何证明。外接生成式视频不能自动成为物理可信渲染；若改变轨迹或结构，只能另标为艺术衍生品，不能替代模拟视频。
- 视频过大时按计划允许范围调整编码或显示分辨率，保留完整物理时间；不能通过裁去失败片段、缩短模拟或重算更容易的物理过程来通过交付。
- 用户要求 deep 而能力不足时明确报告；未经允许不把 standard 标成 deep。若选择回退，保留请求质量、实际质量与原因。

## 失败与交付

最多进行两次有具体展示参数变化的修复，每次重新 prepare。远程提交结果未知时先经宿主状态/对账确认，不用新幂等键再次提交同一渲染。

渲染失败时保留完整 `SimulationBundle`、模型依赖和可交付数据，返回独立失败状态；不得将模拟成功描述为视频已完成。输出路径和下载位置由宿主分配，只使用宿主签发的不可变引用。

最终摘要包含用途、视频/数据引用、实际质量/provider、播放时长与渲染边界；物理用途另报告物理时长/映射与数值验证，形状展示明确无模拟和测量。只有视频验证和宿主交付均成功才声称视频已交付；视觉质量不能作为数值结论的证据。
