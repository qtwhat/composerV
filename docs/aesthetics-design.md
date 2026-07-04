# 美学感知 pass 设计(v1)

> 2026-06-23。给 composerV 加一个端上(Apple Vision)美学评分的感知信号:蒸馏成质量标签给导演读、原始分留库给执行。本文是 v1 的设计契约。架构背景见 `STATUS.md`、`vlm-capabilities-and-director-plan.md`。

## 1. 目标与背景

现状:选片/入点完全靠导演(Claude)读文字表凭判断,**没有数字质量信号**;`clarity/keyframes.py` 是均匀抽帧当缩略图,不是挑最好的一刻。Apple Vision 的 `CalculateImageAestheticsScoresRequest`(macOS 15+,端上、毫秒级)给每帧一个 `overallScore`(−1~1,综合曝光/模糊/构图)+ `isUtility`(截图/文档/非记忆类帧)。比问 VLM「好不好看」更确定、可复现,也不占 VLM 限流预算。

v1 用它服务两件事:
- **① 片内最佳入点**:每镜挑最清晰/构图最好的一刻当 in 点。
- **② 弃弱镜**:把模糊/过渡/utility 帧标出来,供导演**软性**舍弃。

## 2. 架构定位(串行、导演为核心、决策/原始数据分离)

管线串行:感知 →(蒸馏)信息表 → 导演决策+下指令 → 机器执行 → 输出。美学信号一分为二:

- **蒸馏质量标签 → 信息表 → 导演**(读了做决策:开最佳 / 弃弱镜)。
- **原始分 + `best_t` → 缓存库**(导演下「开最佳」这类指令后,执行端查 `best_t` 定帧)。

原始数值**不进导演 prompt**(噪音 + LLM 对精确数不可靠)。v1 导演**不加新输出字段**:开最佳用现有 `in_s`,弃弱镜用「不输出该 segment」。导演输出契约扩出构图/裁切指令是 **v2**(方向已确认)。

## 3. 范围

| | 内容 |
|---|---|
| **v1 做** | 端上打分 pass、逐刻蒸馏标签、`best_t`、导演表/prompt 接入、软弃弱 |
| **v1 不做(→v2/后续)** | 裁切判官、saliency pass、导演契约加构图字段、`curve` 的机器端用途(入点像素级细调/关键帧择优/Ken Burns 锚点)、原片高分辨率打分 |

## 4. 数据流

**analyze(一次,缓存)** ── 接在 `clarity/analyze.py` 的 `analyze_clip` 里,VLM 之后:
1. proxy 已有 → 2fps 抽帧到临时目录(复用 `index/frames.py` 的 `sample_frames`,`fps=2`,`--aes-fps` 可调)。
2. `score_frames(paths)` → `{path: (score, isUtility)}`;与帧时间戳拼成 `series = [(t, score, util)]`。
3. `best_t = best_moment(series, duration)`;`series` 整条 + `best_t` 存进新表 `clip_aesthetics`。**不动 `clip_moments` / `ClipMoment`。**
4. 临时帧删除。

**montage(读缓存,导演)**:
5. 装配:读 `clip_aesthetics` 的 `curve`;每个 moment 取 curve **最近样本** → `distill_quality` 出标签,放进 visual 元组 `v[4]`;片头放 `best_t`。
6. `build_footage_table` 渲染标签 + 片头 `best ~Xs`;导演读表 → `in_s` 开在 `best` 附近 / 略过弱镜。

## 5. 组件(各单元:职责 · 接口 · 依赖)

| 单元 | 职责 | 接口 | 依赖 |
|---|---|---|---|
| `swift/aesthetics.swift` | 批量给帧打分 | argv/stdin 帧路径 → stdout JSON `[{path,score,isUtility}]` | Vision,macOS 15+ |
| `.composerv/bin/aesthetics` | 编译产物 | 首次惰性 `swiftc` 构建并缓存 | Xcode 命令行工具 |
| `analyze/aesthetics.py::score_frames(paths)` | 调二进制、解析 JSON;失败返回 `{}` | `list[str] → dict[str,(score,util)]` | 二进制 |
| `analyze/aesthetics.py::distill_quality(score,util)` | 分 → 文字标签(纯函数) | `(float\|None,bool) → str` | — |
| `analyze/aesthetics.py::best_moment(series,dur)` | argmax + 头尾护栏(纯函数) | `(series,float) → float\|None` | — |
| `analyze/aesthetics.py::quality_tag_at(t,curve)` | curve 最近样本 → `distill_quality`(纯函数) | `(float,curve) → str` | — |
| `clarity/analyze.py::analyze_clip` | 串 2fps 抽帧→打分→写库 | 注入 `score_fn`(测试) | `sample_frames`,store |
| `store/db.py` | 新表 `clip_aesthetics(asset_path PK, best_t REAL, curve TEXT)` + `set_/get_clip_aesthetics`(`curve` = JSON `[[t,score,util],…]`);**不动 `clip_moments`/`ClipMoment`** | 新表 `CREATE IF NOT EXISTS` | — |
| `director/table.py::build_footage_table` | 渲染逐刻标签 + 片头 `best` 行 | 读 visual 元组 `v[4]`=quality 标签(`v[3]`=objects 仍忽略,与既有 objects-容忍测试不冲突);clip dict 读 `best_t` | — |
| `director/prompt.py::_RULES` | 扩规则 8 与 2·10 | — | — |
| `director/montage.py` 装配处 | `:84` → `(m.t,m.text,m.ocr,m.objects, quality_tag_at(m.t,curve))`;`:93` rows dict 加 `best_t`(读 `get_clip_aesthetics`) | 调用点已定位 | store, `aesthetics.py` |

## 6. 蒸馏规则(起点值,可调;存原始分以便改阈值不必重跑感知)

- **逐刻标签**:`score ≥ 0.4` → `[清晰·构图好]`;`isUtility` 或 `score ≤ −0.2` → `[弱/过渡]`;中间 → **不打标签**(只标显著的,避免每行有字 = 噪音)。
- **best_t**:`argmax(score)`,排除头尾 0.3s,要求 `≥ 0`,否则 `None`(不硬塞)。
- **导演 prompt**:
  - 规则 8 扩:visual 行可能带质量标签、片头带 `best ~Xs`;优先把镜头开在最佳/最清晰一刻。
  - 规则 2/10 扩:标 `[弱/过渡]` 的是弱素材,优先不开在那或舍掉;**但规则 1(human-led)永远优先** —— 被人标重要的、哪怕拍糊也留。

## 7. 容错 / 降级

- 二进制缺失 / 构建失败 → `score_frames` 返回 `{}` → 无标签、`best_t=None`,管线照常(优雅降级,类比 grounding 失败不致命),stderr 告警。
- 单帧打分失败 → 跳过该帧留其余。
- `quality`/`clip_aesthetics` 为空 → 表渲染就不加标签,导演退回纯文字判断。
- 不静默:失败打 stderr(沿用 `claude_cli` / `analyze` 约定)。

## 8. 测试

纯函数单测(不依赖二进制,注入分数):
- `distill_quality`:各档分 → 标签。
- `best_moment`:argmax、头尾护栏、全低 → `None`。
- `build_footage_table`:带 `quality` → 行尾标签;带 `best_t` → 片头 `best` 行。
- `prompt`:`_RULES` 含新规则。

集成(真实帧,类比 `test_reframe.py` 对 ffmpeg 的验证):swift 二进制构建 + `score_frames` 在几张样本帧上出分。

## 9. 配置 / CLI

- `analyze` 默认开美学(便宜,不需 `taskpolicy` 限流);`--no-aesthetics` 关;`--aes-fps N` 调网格密度(默认 2)。

## 10. 未决 / v2 路线

- **v2 裁切判官**:需 saliency pass(同一个 swift 二进制加子命令)+ 导演输出契约扩 `crop`/`focus` 指令。
- **`curve` 的机器端用途**:入点像素级细调、关键帧择优、Ken Burns 锚点。
- **模糊精度**:代理图(1280×720)会低估模糊,后续可改从原片高分辨率帧打分。
- **benchmark**:2fps 网格对 `analyze` 全程耗时的影响(预期可忽略,VLM 才是瓶颈)。
