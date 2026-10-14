# 单图流匹配建模研究与 Mac 本地实现

更新：2026-10-01；首轮算法来源检查于 2026-09-30，前景分割来源检查于 2026-10-01。目标设备：Mac mini M4、16 GB 统一内存。第一阶段目标是将公开权重的真实图像到三维推理接入 QQ；自有数据训练是后续独立研究阶段。本文件记录方法选择和证据边界；本机验收结果以部署记录和实际生成产物为准。

## 1. 结论与证据等级

采用 **Tencent Hunyuan3D-2mini 标准形状权重 + MLX 形状推理适配器**，先输出无贴图 GLB/OBJ。LLM 负责理解请求、选择图片和参数、解释结果；三维生成由图像条件流匹配模型完成。运行时只读取本地权重，不临时下载模型，不调用 Meshy 收费 API。

MLX 实现来源为独立作者的 [Hunyuan3D-Swift/Python](https://github.com/ZimengXiong/Hunyuan3D-Swift/tree/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/python/shape)，审计提交 `292331f4d26ddb80b9dcea6bcb5629ff82f12b82`。它不是腾讯官方 Mac 后端。选它的依据是可读代码、原权重直接加载、CPU 网格提取以及作者对原 PyTorch 实现的数值对照。是否可靠适用于本机，要由完整推理验收决定。

| 主张 | 等级 | 可支持的结论与边界 |
| --- | --- | --- |
| 条件流匹配目标可学习给定概率路径的速度场 | 有明确假设的理论结论 | 原论文梯度等价定理需要密度与正则性条件；不保证有限模型、离散积分或单张照片得到真实背面 |
| Hunyuan 权重用于真实形状生成 | 可核对的模型与代码事实 | 加载公开 VAE、DINO 和 DiT；不是随机未训练网络或程序化玩具 |
| MLX 适合 16 GB Mac 的第一候选 | 条件性工程判断 | 作者测量和源码支持可行性；不是已证明的内存上界或本机速度承诺 |
| 可渲染网格可以直接用于仿真 | 不成立 | 尺度、材料、拓扑、碰撞代理和求解器契约仍需验证 |
| 学习流匹配公式即可复现 Meshy 商用品质 | 无证据支持 | 数据、表示学习、拓扑解码和训练规模同样决定效果 |

## 2. Meshy 公开了什么

[Meshy T2 技术报告 v1，2026-07-28，§2.1–2.3](https://arxiv.org/html/2607.28675v1#S2)披露了两级流匹配：图像先生成 `64³` 占据脚手架，再与图像共同条件化顶点潜变量。Mesh VAE 为每个顶点配置连续潜变量，联合解码坐标、边和有向面的邻接顺序；不是仅对坐标去噪。顶点预算影响复杂度，OT 分配的空间位置编码帮助潜变量对应。训练资料必须包含可恢复拓扑的网格，非流形输入还需修复。这里的关键增量是**表示和拓扑解码**。

[官方仓库 Roadmap](https://github.com/meshy-dev/meshy-t2#roadmap)在本次检查时仍以准备开放代码、权重为路线图，未找到可安装的完整推理代码和检查点。[Meshy 自己的 T2 介绍](https://www.meshy.ai/blog/meshy-t2-native-3d-mesh-generation)中的时延属于作者实验描述。[商业 Meshy 7 的图像对齐介绍](https://www.meshy.ai/blog/meshy-7-image-to-3d-geometry-alignment)没有公布整套训练与部署细节。因此项目可研究 T2 的公开方法，但当前接入不能命名为“Meshy 复现”，也不能把 T2 论文等同于商业服务完整算法。

## 3. 流匹配的实际数学目标

令 `c` 为图像特征，`z` 为真实形状经过 VAE 编码、缩放后的潜变量，`ε ~ N(0,I)`，`t ~ U[0,1]`。采用噪声到形状方向的线性路径：

\[
x_t=(1-t)\varepsilon+t z,
\qquad u_t=z-\varepsilon,
\]

\[
\mathcal L_{\mathrm{CFM}}(\theta)
=\mathbb E_{c,z,\varepsilon,t}
\left\|v_\theta(x_t,t,c)-(z-\varepsilon)\right\|_2^2.
\]

这是 [Hunyuan3D 2.1 报告 v1，§3.1.2、式 (2)](https://arxiv.org/html/2506.15442v1#S3.SS1.SSS2)所使用的线性条件流目标；这里明确使用 `x₀=噪声、x₁=数据` 的方向。其他论文可能反向定义时间，速度符号和积分方向必须一起核对。Hunyuan 的学习对象是形状潜变量，不是直接具有物理单位的顶点。

推理通过速度积分完成。Euler 离散形式和 classifier-free guidance 为：

\[
x_{k+1}=x_k+(t_{k+1}-t_k)v_\theta(x_k,t_k,c),
\qquad
v_{\mathrm{CFG}}=v_{\emptyset}+s(v_c-v_{\emptyset}).
\]

此处 `x` 和速度属于归一化潜空间，`t` 无量纲；`Δt × v` 与 `x` 维度一致。完美速度 `z−ε` 在直线路径上的积分正好从 `ε` 到 `z`，是一个必要的符号检查。实际推理的 `z` 未知，网络预测的是条件速度场，有限步长、模型误差和较大的 `s` 都会改变采样分布。

[Flow Matching for Generative Modeling v2，§3.2、定理 2，§4.1 式 (20)–(23)](https://arxiv.org/html/2210.02747v2#S3.SS2)给出条件与边缘目标的梯度等价条件。带非零端点方差的路径可写为 `x_t=[1−(1−σ_min)t]ε+tz`，目标速度为 `z−(1−σ_min)ε`。零端点方差是常用极限，但不能忽略定理中的密度条件。

一个有限的推导：给定 `(x,t,c)`，平方损失的最优预测是条件均值 `E[z−ε | x,t,c]`，因为平方误差可分成条件方差与偏离条件均值的平方。它解释了为何不必知道推理样本的目标 `z` 也能训练速度场；它没有证明训练收敛、解码网格有效或视觉尺寸正确。单张图像的遮挡背面本身具有多解，生成结果是条件分布中的一个样本。

安装版本的 [MLX sampler.py](https://github.com/ZimengXiong/Hunyuan3D-Swift/blob/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/python/shape/hy3dmlx/sampler.py)实现噪声到数据积分、上述 CFG 顺序和统一 seed。标准 mini 的 sigma shift 为 1；当前 pipeline 未向标准 sampler 传入其他配置的 shift，因此不应无验证地替换自定义调度器。相同数字 seed 在 MLX 与 PyTorch 中不意味着相同初始噪声；跨后端数值对照需固定噪声张量。

## 4. 后端比较与选型

| 方法 | 公开表示与运行条件 | 当前用途 |
| --- | --- | --- |
| Meshy T2 | 顶点集合 VAE、占据流、拓扑流；本次未找到完整开放权重 | 研究表示与原生低面数拓扑 |
| TRELLIS | 稀疏结构 + SLAT；官方测试 Linux、NVIDIA ≥16 GB、CUDA 扩展 | 有价值的独立训练参考，不作为当前 Mac 已安装后端 |
| TRELLIS.2 | O-Voxel 几何与 PBR；4B、官方 Linux/NVIDIA ≥24 GB、CUDA 12.4 | 后续高质量表示研究，当前资源不匹配 |
| Hunyuan3D-2mini | 潜变量形状流、DINO 条件、VAE 隐式表面解码；0.6B 是 DiT 规模 | 当前标准形状权重 |
| Hunyuan3D-2 / 2.1 | 更大形状模型及独立材质管线 | 质量与速度比较候选，先完成 mini 验收 |

TRELLIS 的系统要求与核心许可来自[官方 README 的 Prerequisites 和 License](https://github.com/microsoft/TRELLIS#prerequisites)；其双阶段表示来自[报告 v1 §3.3](https://arxiv.org/html/2412.01506v1#S3.SS3)。TRELLIS.2 的系统条件来自[官方 README](https://github.com/microsoft/TRELLIS.2#prerequisites)，O-Voxel 和分阶段流来自[报告 v1 §3](https://arxiv.org/html/2512.14692v1#S3)。两者主代码和模型采用 MIT，但各 CUDA 子模块存在独立许可；没有证据把官方 CUDA 路径宣称为可直接运行的 MPS 路径。

## 5. 可复现的 MLX 形状推理

### 权重与源码

使用 [Tencent 官方 mini 目录](https://huggingface.co/tencent/Hunyuan3D-2mini/tree/main/hunyuan3d-dit-v2-mini)中的两个文件：

```text
<local-model-root>/hunyuan3d-dit-v2-mini/
  config.yaml
  model.fp16.safetensors
```

`model.fp16.safetensors` 约 3.82 GB，包含 DINO conditioner、DiT 和 VAE；同目录约 3.82 GB 的 `.ckpt` 是重复存储格式，本次不需要。无需另下 DINO 巨型权重。下载时应固定模型仓库 revision，记录文件字节数、SHA256、代码 SHA 和许可版本；Git 只管理清单与适配器，权重置于本机外部缓存。

首轮本地安装的权重清单来自实际推理 receipt：仓库 revision `f90a0f7df7d5e6f71109cf333f6a95a0ae3194a6`；config 1628 字节，SHA256 `cabcba7f6115752c8fe5b370e12bf714936f70377a8a80f151872f76c2d64609`；safetensors 3819958234 字节，SHA256 `3cc66f3bea33e4062b7dbc875ffe1d70c4888914aec3e91b60f94e9bd01b522b`。它们是已安装文件的来源记录，不把可变化的 main 当作固定版本。

[mini config.yaml](https://huggingface.co/tencent/Hunyuan3D-2mini/raw/main/hunyuan3d-dit-v2-mini/config.yaml)定义 512×64 潜变量、DiT 宽度 1024、DINO 隐藏维度 1536 和 518 图像输入。Mini 指 DiT，不能用 0.6B×2 字节估算整套内存。

[convert.py](https://github.com/ZimengXiong/Hunyuan3D-Swift/blob/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/python/shape/hy3dmlx/convert.py)按原检查点键名前缀读取权重，检查每个推理模块所需键和形状；卷积权重按 MLX 布局转置，训练用 VAE encoder 参数不会参与解码。加载过程存在初始化及转换临时分配，因此磁盘字节数不能当作加载峰值。

### Python 入口

这是上游 MLX 库的实际调用契约，不是 QQ 的公开参数格式。`model_dir` 必须指向包含两个文件的子目录；`generate` 返回 `trimesh.Trimesh`。

```python
import mlx.core as mx
from hy3dmlx.pipeline import Hunyuan3DShapePipeline

pipeline = Hunyuan3DShapePipeline.from_pretrained(
    model_dir,
    dtype=mx.float16,
    quantize=None,
)
mesh = pipeline.generate(
    foreground_rgba_path,
    num_inference_steps=30,
    guidance_scale=5.0,
    octree_resolution=128,
    num_chunks=8000,
    seed=1234,
    border_ratio=0.15,
    octree_decode=False,
    compile_dit=False,
)
mesh.export(output_glb_path)
```

CLI 在该仓库 `python/shape` 目录中运行：

```sh
python -m hy3dmlx.pipeline foreground.png \
  --weights /absolute/path/hunyuan3d-dit-v2-mini \
  --out /absolute/path/output.glb \
  --steps 30 --guidance 5 --octree 128 --dtype float16 --seed 1234
```

入口实现见 [pipeline.py](https://github.com/ZimengXiong/Hunyuan3D-Swift/blob/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/python/shape/hy3dmlx/pipeline.py)。先用 dense 128 建立基线，再比较 256、8-bit 和 octree。量化、稀疏解码、编译必须分别记录；改变数值的优化不能与未优化样本混作同一次重现。基础 shape 管线没有纹理生成。

### 依赖与环境

[上游 pyproject.toml](https://github.com/ZimengXiong/Hunyuan3D-Swift/blob/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/python/shape/pyproject.toml)声明 Python ≥3.12；[uv.lock](https://github.com/ZimengXiong/Hunyuan3D-Swift/blob/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/python/shape/uv.lock)中的版本为 MLX/MLX Metal 0.31.2、numpy 2.5.0、opencv-python 4.13.0.92、Pillow 12.2.0、PyYAML 6.0.3、safetensors 0.8.0、scikit-image 0.26.0、trimesh 4.12.2。它还包含下载/绘图脚本依赖。这是上游声明，不是本机已测试的环境承诺。部署必须独立虚拟环境，实际安装后保存锁与 Python/macOS 版本；不能为了满足另一项目的锁升级 QQ 客户端的全局环境。

Tencent PyTorch 备用形状 API 是 `Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(local_snapshot, subfolder='hunyuan3d-dit-v2-mini', use_safetensors=True, variant='fp16', device='mps', dtype=...)`。官方 [gradio_app.py](https://github.com/Tencent-Hunyuan/Hunyuan3D-2/blob/main/gradio_app.py)将 CPU/MPS 配置为普通 Marching Cubes。该路径可用于固定输入张量的算子对照；完整服务器混入纹理和 CUDA 功能，不宜直接作为本项目 Mac 服务。

### 图像预处理

[MLX preprocess.py](https://github.com/ZimengXiong/Hunyuan3D-Swift/blob/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/python/shape/hy3dmlx/preprocess.py)基于 alpha 包围框裁剪居中、前景约占 85%、合成白背景，先到 512，再到 DINO 的 518 分辨率和 ImageNet 归一化。它**不会自动抠图**；RGB 转 RGBA 仅补全不透明 alpha。主机必须明确生成或接受前景 mask，拒绝空 mask、过小主体、破损文件和超过限制的像素量。不能把照片尺寸转换为米。

建议输入是清晰单物体、完整轮廓、尽量少遮挡的照片。对多个物体、镜面、透明材质、极细结构和背面形状，返回结果时保留不确定性说明。保留原图 hash、foreground mask、预处理版本和最终条件图，方便诊断“像不像”的误差来源。

### JPEG 的离线前景分割

本项目增加独立 `modeling_flow/segmentation.py`，使用本机 ONNX Runtime 的 `CPUExecutionProvider` 推理 U2-Net。透明输入保留现有 alpha；不透明 JPEG/RGB 才进入分割。模型由操作者预装，路径和 SHA256 固定，推理函数没有模型下载或 rembg 运行时导入。

原始 [U2-Net 作者仓库](https://github.com/xuebinqin/U-2-Net/tree/ac7e1c817ecab7c7dff5ce6b1abba61cd213ff29)采用 Apache-2.0。这里使用 rembg 作者发布的 ONNX 转换文件，其[固定下载地址](https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx)对应 asset 85857932、175997641 字节。[rembg U2-Net session](https://github.com/danielgatis/rembg/blob/202e42649a8492a7c49f808de36608a7d1cbbfe3/rembg/sessions/u2net.py)公布预期 MD5 `60024c5c889badc19c04ad937298a77b`；发布元数据没有 SHA256。安装器先核对来源 MD5/大小，再保存计算所得 SHA256，不能声称该 SHA 是上游发布的签名。

预处理依据 [rembg BaseSession.normalize](https://github.com/danielgatis/rembg/blob/202e42649a8492a7c49f808de36608a7d1cbbfe3/rembg/sessions/base.py)：RGB Lanczos 到 `320×320`，按该图最大强度归一化，再进行 ImageNet mean/std 变换。取首个 saliency 输出做 min/max 归一化、软 alpha 和原图尺寸的 Lanczos 插值。这不是精确的物体识别或毛发级 matting；均匀、非有限、空或尺寸不符的结果明确失败，不能静默回退成整张照片。

本项目用验证过的模型字节构建 ONNX session，避免隐式读取外部 initializer 文件。receipt 记录模型 hash、CPU provider、预处理版本、mask 包围框、占比和未验证假设。源码参考的 rembg commit 为 `202e42649a8492a7c49f808de36608a7d1cbbfe3`，其实现许可是 [MIT](https://github.com/danielgatis/rembg/blob/202e42649a8492a7c49f808de36608a7d1cbbfe3/LICENSE.txt)；U2-Net 模型来源与 ONNX 转换来源分别保留。单元测试覆盖协议和数值处理，真实照片的分割质量仍需实际权重验收。

2026-10-01 本机独立前景验收：官方 `004.png` 合成白背景、JPEG 编码后经过 QQ 实际归一化，800×800 输入 alpha 为 255；CPU U2-Net 输出 alpha 范围 0–255、非零占比 32.23%。以 127 为阈值与该输入原有 alpha 比较，轮廓 IoU 为 0.99645；含 hash 和模型加载耗时 2.320 秒，进程 `RUSAGE_SELF` 峰 RSS 1.582 GB。实际安装 ONNX Runtime 1.24.4，ONNX 的 SHA256 为 `8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491`，与已查阅来源的 MD5/字节数相符。实测摘要汇总于 [Mac 验证记录](modeling-flow-validation.md)。这验证一个不透明 JPEG 的处理链，不能推广为复杂背景、多个物体或毛发边界的质量保证。

## 6. 资源预算与验收设计

[作者 BENCHMARKS.md](https://github.com/ZimengXiong/Hunyuan3D-Swift/blob/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/python/shape/BENCHMARKS.md)报告的设备是 48 GB Apple M 系列，mini FP16 常驻约 3.82 GB、推理峰约 5.20 GB，8-bit 较低；时间和质量比较使用特定样图。审计 [measure_mlx.py](https://github.com/ZimengXiong/Hunyuan3D-Swift/blob/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/python/shape/scripts/measure_mlx.py)发现其先释放加载临时对象、重置峰值，再运行一次 CFG、VAE 解码及 128 网格；不包含完整加载、真实 DINO 编码和 30 步全流程。因此这些数字是可行性线索，不能承诺 M4 的总 RSS、吞吐或质量。

本项目首轮实验设计（工程验收方案，不是已完成结果）：

1. 一次只允许一个推理子进程；QQ 监控继续响应，重复请求排队或返回繁忙状态。模型进程崩溃、超时或取消后由宿主回收，不能反复同时加载。
2. 先测 FP16、30 步、dense 128、固定 seed；记录下载与冷加载之外的推理时间，也分别记录冷加载时间。Mac 16 GB 保留系统与 QQ 空间；完整流程连续采样 RSS、MLX active/peak、内存压力和 swap 增量，不能只在结束时读峰值。
3. 成功必须产生非空、有限坐标、有效索引的网格，GLB 可重新打开，并能输出多视角预览；错误必须有明确阶段、原因和可追踪日志。闭合程度、连通分量、面数和包围盒写入结果，不能只检查文件存在。
4. 三个以上独立物体（圆滑、硬表面、细结构）和至少两个 seed。预先设定可以接受的时间与内存范围；保存所有尝试，包括空网格、OOM、长时间挂起和差质量。
5. 建立基线后逐项比较 dense 256、8-bit、octree。质量使用成对多视角、轮廓 IoU、组件数和网格诊断；有真实几何时再测归一化 Chamfer、法线一致性。不能用单图的主观漂亮程度替代几何测量。

Dense 采样格点量约为 `(R+1)³`：128 约 215 万点，256 约 1700 万点。分块限制神经查询激活，不能消除 CPU 网格和最终网格所占内存；`num_chunks` 在该 API 实际表示单次查询点数。Octree 只解码近表面带，是额外近似，必须对细结构验证。FP32 权重会大约倍增权重存储，不能把它作为 16 GB 的默认保险措施。

## 7. 与热插拔建模、模拟、渲染的边界

建模应具有两个显式产物方向：

```text
图片 → foreground → image encoder → shape flow → VAE → visual mesh
                                                            ↓
                                            GLB/OBJ + metadata → 渲染预览
                                                            ↓
                            指定尺度/材料/运动 → 简化或碰撞代理 → 验证 → 模拟
```

新 skill 的生成成功意味着 `visual_asset_ready`，不自动意味着 `ready_to_simulate`。现有物理 scene 的 `mesh_asset` 上限为 4096 顶点、8192 三角形，并要求一个连通、一致绕序的表面，运行时还检查退化、流形和自相交。生成网格可能拥有十万级面或多个分量，不能直接塞进此契约。

渲染允许展示详细视觉资产；模拟需要单独的尺寸标定、碰撞/求解几何和物理参数。简化必须保存原始视觉网格并记录误差，不得静默抛弃细杆或其他连通部件。PBR metallic/roughness 是外观参数，不能直接转换为杨氏模量、密度或导热率。LLM 只能提出可审查的假设，材料与尺度来源必须标明。

宿主维持任务编号、图片副本、超时、取消、并发限制、路径与结果验证；skill 内部负责预处理、后端推理、导出和诊断。权重、虚拟环境和研究代码可替换，但外部请求/结果契约保持稳定。Git 保存独立适配器、skill manifest、文档和模型来源清单；大权重及用户图片不进入代码仓库。

## 8. 自有流匹配训练路线

这是后续研究计划；当前接入只使用公开预训练推理。先确定是否微调 Hunyuan 派生模型，还是训练独立模型，两条路线的数据与许可不能混用。

**数据单位与隔离。** 一条训练记录应是一件具有可证明使用权的三维资产，而不是一张渲染图。记录来源、作者、许可、原始文件 hash、拓扑修复过程、单位/尺度、归一化变换、类别、渲染相机、灯光、背景、mask 和资产族编号。一个资产的所有视角和变体只进入一个集合。用几何近重复/来源族进行分组，再切训练、验证与最终测试；否则多视角泄漏会伪造泛化能力。

**先建立低成本基线。** 使用自有程序化形状及明确许可网格，渲染图片，先比较参数化几何/最近邻检索与小型条件流。小型实验验证符号、缩放、条件响应、保存恢复和指标管线，不宣称通用识图能力。按资产复杂度、遮挡、材质和未见类别分别报告失败率。

**分离表示误差。** 先测 VAE 对真实网格重建的误差与无效拓扑率，建立重建上限；再训练图像条件流并与无条件、打乱条件、未微调公开权重作比较。若真实形状在 VAE 中已经丢失细节，调流匹配损失无法补救。评估固定计算与相同数据机会，多 seed，保存未挑选样本，最终测试集只在方案固定后使用。

**训练可复现清单。** 保存 latent 的 shape/scale、噪声分布、t 分布、目标速度方向、loss 是否按维度平均、CFG 条件丢弃率、优化器/学习率、batch/累积、混合精度、随机 seed、数据版本、验证频率、checkpoint 和恢复状态。明确比较数据准备、VAE、图像编码、流训练、采样与人工验收的总成本，不能只算 DiT 推理时间。

**资源分阶段。** 16 GB M4 先用于数据检查、推理和小型实验。完整大模型从头训练的规模不能从“推理能运行”推出。Hunyuan 与 TRELLIS 的训练需要大规模资产和加速资源；独立原生拓扑流又需要带拓扑监督的表示研究。未来扩展算力前先完成有限、可证伪的实验和质量门槛，不在当前 QQ 接入阶段启动长训练。

**数据许可隔离。** Tencent 许可 §5(b)禁止将其 works、output 或结果用于改进无关 AI 模型。独立模型训练集应来自原始授权资产及本项目独立渲染，不能用 Hunyuan 生成网格建立伪标签库；Hunyuan 微调仍是受其许可约束的派生路线。Meshy 的公开报告也不是训练数据授权。

## 9. 许可和发布记录

本次查阅 [Tencent Hunyuan3D-2 根 LICENSE](https://github.com/Tencent-Hunyuan/Hunyuan3D-2/blob/main/LICENSE)，名称是 **Tencent Hunyuan 3D 2.0 Community License Agreement**，不能依据旧文件头误报为 MIT 或一概“仅非商用”。相关条款：§1/§5 地域不含欧盟、英国、韩国；§3 提供现行许可副本、修改通知及适用 Notice，并向第三方用户披露实际服务提供者完整法律名称、明确腾讯无关联/赞助/背书；§4 的百万 MAU 条件按许可文字判断；§5(b) 限制改进其他 AI；§6(d) 腾讯不主张输出权利。完整条款及 AUP 仍以源许可为准。

MLX 作者代码使用[该提交的 MIT LICENSE](https://github.com/ZimengXiong/Hunyuan3D-Swift/blob/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/LICENSE)。MIT 不能覆盖 Tencent 权重或派生模型条件。项目自己的适配器、权重和第三方推理源码必须分别标注来源；公开 GitHub 仓库不包含模型大文件。接入 QQ 的用户结果标明 AI 生成、所用模型与实际服务提供者；对照片推导的几何和物理假设可追踪。

发布清单应记录：项目 commit、adapter SHA、实际推理代码 SHA、模型仓库 revision、config/checkpoint SHA256 和字节数、依赖锁、机器/OS、参数、预处理版本、输入 hash、耗时/完整内存测量、网格诊断、许可证来源与安装时副本。可重现不仅是固定 seed，还包括这整条链。

## 10. 源码与论文核查索引

| 来源与精确位置 | 版本/检查日期 | 支持的内容 | 仍需验证 |
| --- | --- | --- | --- |
| [FM §3.2 定理 2 / §4.1](https://arxiv.org/html/2210.02747v2) | v2，2023-02-08 | 条件目标梯度等价、线性概率路径 | 有限网络与积分质量 |
| [Hunyuan2.1 §3.1.2 式 (2)](https://arxiv.org/html/2506.15442v1) | v1，2025-06-18 | 图像条件形状流目标 | 2.1 的结果不自动移植到 mini |
| [Meshy T2 §2.1–2.3](https://arxiv.org/html/2607.28675v1) | v1，2026-07-28 | 网格 VAE、两级流与拓扑解码 | 完整代码/权重及商业服务关系 |
| [Tencent mini 配置/权重目录](https://huggingface.co/tencent/Hunyuan3D-2mini/tree/main/hunyuan3d-dit-v2-mini) | main 查于 2026-09-30；安装需锁 revision | 实际 bundle、模型结构 | 下载完整性和本机生成 |
| [MLX Python shape 源码](https://github.com/ZimengXiong/Hunyuan3D-Swift/tree/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/python/shape) | SHA `292331f4…` | 可审计 Mac 推理实现 | 本机完整流程、资源峰值和样本质量 |
| [MLX 内存测量脚本](https://github.com/ZimengXiong/Hunyuan3D-Swift/blob/292331f4d26ddb80b9dcea6bcb5629ff82f12b82/python/shape/scripts/measure_mlx.py) | 同一 SHA | 作者内存数字的测量范围 | 宿主 RSS/加载/DINO/多步最大值 |
| [Tencent 当前许可 §1、3–6](https://github.com/Tencent-Hunyuan/Hunyuan3D-2/blob/main/LICENSE) | main 查于 2026-09-30 | 模型使用、服务与训练限制 | 发布时重新保存现行文本 |
