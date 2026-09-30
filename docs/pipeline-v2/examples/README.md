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
