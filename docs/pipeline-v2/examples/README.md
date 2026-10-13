# 契约示例

这些文件仅用于字段与协议校验。所有 ID、hash、时间、估价、验证通过标记均为虚构，没有对应数据文件、作业、付费调用或交付回执。不要把它们导入运行时注册表或当作物理验证证据。

| 文件 | 展示内容 |
| --- | --- |
| [pipeline-request.json](pipeline-request.json) | 标准建模、strict 周期观测、普通渲染、视频和数据 |
| [steady-request.json](steady-request.json) | PCB 稳态的物理时长/采样间隔为 null，视频时长独立 |
| [rerender-request.json](rerender-request.json) | 重用模型和模拟，permitted_stages 只允许渲染，改变相机灯光 |
| [pipeline-lock.json](pipeline-lock.json) | 默认三阶段的原子版本组合 |
| [plan-response.json](plan-response.json) | 规范化参数、状态规格、实际资源限制、提交幂等键与 next_action |
| [insufficient-cache-response.json](insufficient-cache-response.json) | 抽样显示帧不足以满足新渲染要求时的结构化拒绝 |
| [simulation-bundle.json](simulation-bundle.json) | bundle envelope 与来源、采样、实际质量；不是完整领域产物 |
| [delivery-manifest.json](delivery-manifest.json) | 视频有回执、数据回执未知，整体交付保持 reconciling |

模板与 metric ID 也都是拟议目录条目。实际能力与完整参数只能由已实现模块的 capabilities/template 公布；schema 通过不能证明模板存在、文件 hash 正确、预算可用或物理结果可信。

## 2026-10-13 草案用途与依赖示例

新增字段仍属于未注册、未部署的 v2 草案，不改变当前 v1 公开工具。此前草案 fixture 已显式补 purpose 与依赖锁；旧草案 JSON 需要按新字段更新，不能直接送进现有 v1 runtime。

| 文件 | 展示内容 |
| --- | --- |
| [physical-model-bundle.json](physical-model-bundle.json) | 强类型物理模型、identity 几何绑定与 engine dependency |
| [physical-render-bundle.json](physical-render-bundle.json) | 与模拟上游一致的 model_ref/hash/engine，物理播放映射 |
| [model-preview-request.json](model-preview-request.json) | model_preview 省略 simulation 和模拟质量/阶段，shape_only 呈现 |
| [model-preview-plan-response.json](model-preview-plan-response.json) | 无模拟 pin/状态的模型展示计划，继续使用 pipeline_submit |
| [model-preview-model-bundle.json](model-preview-model-bundle.json) | model_unit raw/display 资产、强类型 identity 绑定；无物理资格声明 |
| [model-preview-render-bundle.json](model-preview-render-bundle.json) | 无 simulation_ref、无物理时间、无测量的形状视频 metadata |
| [model-preview-data-package.json](model-preview-data-package.json) | 不需要 simulation_ref 的 raw/display 模型数据清单 |
| [stage-task.json](stage-task.json) | 与已接受 plan 一致的模块 pin、engine dependency 和只读输入挂载 |

这些文件的 hash 仍是虚构占位值。contract_semantics 的 fixture resolver 只按显式传入的示例索引比较关系，不核验 manifest 或文件的真实字节摘要，也不签发权限。真实 host 必须另做授权、文件/hash、几何证明、状态覆盖与交付回执验证。
