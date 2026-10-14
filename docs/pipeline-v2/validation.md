# P0 契约检查记录

日期：2026-09-26，规划阶段历史记录。范围仅为规划、skill、机器接口与示例；该阶段没有实现或运行 v2 pipeline，没有更新活动 toolbox，没有发送 QQ 消息，没有调用付费 provider。**后续实现与真实 1.4.0 验收见 [实现报告](implementation-report.md)**，不要把本记录误读为当前实现状态。

## 已完成的检查

- 对当前代码进行建模、渲染/数据、host/注册表三个独立只读审计，规划中的“现状”据此记录。
- 三份新 skill 通过 skill-creator 的 `quick_validate.py`；现有同名生产 skill 未被替换。
- JSON Schema 通过 Draft 2020-12 自校验；8 份协议示例和 3 份 toolbox 契约通过 envelope 校验。
- 19 个 API 的参数/响应引用、三个 toolbox 的操作集合、示例 next_action 参数均可解析且一致。
- 独立建模计划可不带 solver/renderer pin 和状态输出；不需要虚构后续阶段。
- 14 项反例检查拒绝错误质量维度、未知字段、非法 FPS、空阶段、错误保存规格、路径穿越、错误摘要格式、无错误详情的失败、错误 poll/response 动作、建模伪造状态、稳态伪造物理时长、提前宣布渲染质量、错误 bundle 类型。
- 所有文档本地链接与内部 schema 引用可解析。

独立弱 agent 场景审查覆盖两条请求：“旧液体视频数据、禁止重算但要求深度渲染与逐粒子速度”和“严格周期数据、视频已发送但数据回执未知”。预期决策分别是报告缺失数据且不重算，以及保持 delivery reconciling、核对原动作而不重复发送。

评审发现并已修正：异步阶段调用后的等待步骤、后验查询范围、历史引用来源与禁止重算、visual 数值警告的合法交付、独立阶段字段、状态保存规格、镜头/灯光参数入口、稳态语义、ZIP manifest 循环 hash。

## 复核方法

使用具有 `jsonschema` 的 Python 环境，在仓库根目录执行：

```sh
python3 docs/pipeline-v2/validate_contracts.py
```

这是文档维护工具，不执行求解器、网络请求、注册表操作或交付。本轮依赖安装在临时目录，未写入项目依赖；相同环境可用：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/private/tmp/pipeline-v2-validation python3 docs/pipeline-v2/validate_contracts.py
```

skill 的 frontmatter 校验使用 skill-creator 自带工具与 PyYAML。行为审查不能由 frontmatter 校验替代。

## 验证边界

所有示例都使用虚构 ID/hash/费用/回执，不对应真实产物。schema 只证明结构；动态模板参数、权限、实际文件 hash、几何/数值正确性、跨文件一致性、时间覆盖、预算和发送回执仍需实现相应 validator 后验收。bundle metadata 的 domain 细节由产物契约规定，后续要按真实领域能力细化 schema。

未运行已有完整物理测试集，因为本轮没有更改 solver、运行时、生产 skill 或 v1 toolbox；文件格式校验不应被描述成三模块已经可运行。P1–P6 的完成门列在规划书中。

## 2026-10-13 草案契约复核

v2 状态仍是设计草案；本次只在隔离源码目录修改 docs/pipeline-v2。未运行求解器、读取私有 runs/runtime、操作注册表、调用 provider 或发送 QQ。

本次增加显式 purpose、无模拟的模型展示分支、GeometryAsset/GeometryBinding、按 kind 分派的强类型 metadata、求解器 DependencyPin 和跨字段设计检查。19 个公开工具名不变；rendering_prepare 通过 purpose 选择用途，模型展示禁止 simulation_ref，物理用途要求一致上游模型和引擎依赖。StatePlan 结构额外约束总体选择/full_state 来源，真实数组时间与字段覆盖仍由后续领域 validator 验证。

完整复核需要 jsonschema（本轮使用隔离环境的 4.26.0）：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/pipeline-v2/validate_contracts.py
```

缺少依赖时可明确选择仅标准库检查；此模式不会声称 JSON Schema 合规，也不运行结构反例：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/pipeline-v2/validate_contracts.py --semantic-only
```

正式 jsonschema 4.26.0 结果：19 份正例/模块契约、36 项 schema 反例、17 项结构合法但跨字段非法的反例、19 个操作及其 next_action 示例参数、112 个内部引用全部通过；单独建模与 rendering_prepare 的两种用途均通过。`--semantic-only` 同样通过标准库检查并明确打印 schema/结构反例已跳过。`git diff --check -- docs/pipeline-v2` 通过。

源码链接检查明确不读取 implementation-report 中六个指向历史 runs/ 的私有/生成证据链接；它们是否存在或真实不属于这次契约校验。

[contract_semantics.py](contract_semantics.py) 是设计 fixture 的跨字段检查，不是运行时授权器或几何验证器。它可检查模型/引擎引用一致、资产 pin 与文件声明一致、identity/刚性变换的字段约束、时间窗/重复字段、重复 artifact ID/path；不会读取真实工件或证明 barycentric、exact_subset、bounded_surface 的算法结果。AgentResult 的通用 data 与 next_action dispatch 仍需未来 host 的操作白名单/动态模板/schema/作用域/预算门，不能仅凭本轮检查接入运行时。
