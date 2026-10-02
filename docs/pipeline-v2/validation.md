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
