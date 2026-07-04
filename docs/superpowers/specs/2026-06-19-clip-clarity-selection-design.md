# composerV:Clip 清晰 + 选择 层 — 设计 spec

- 日期:2026-06-19
- 状态:设计已批准,待写实现计划
- 关联:总体计划 `~/.claude/plans/...crystalline-panda.md`(三层架构 index / story / render)

## 1. 背景与重新聚焦

用户有几百 GB 个人视频(两类混合:运动/旅行 GoPro + 家庭生活手机),手动翻看找素材太累,剪辑时怕漏掉好片段(FOMO)。

经过多轮讨论,用户把最大瓶颈收敛到一句话:**「我(用户)看不清这些 clip 到底是什么内容。」** 这比「自动选片」「故事自动化」「人脸/意图标注」都更靠底层,也直接对应最初的痛点。

因此本层的定位:**帮用户快速看清每个 clip 是什么,然后让用户挑出要用的,供后面(用户主导的)创作使用。** 视频理解(语义 + 元数据)存在的意义是为「用户的清晰」和「用户的选择」服务,不是替用户自动剪。

## 2. 目标 / 非目标

**目标**
1. 让用户对任一 clip「秒懂它是什么」。呈现以**文字描述为主**(简短、准确、能区分的摘要),配**关键帧**供一眼核对,辅以**关键事实**(时间/时长/地点/来源)。
2. 让用户**选中** clip,形成一个「工作集」,供后续创作读取。
3. 描述**默认本地生成**(家庭素材画面不出机器);任一条可**一键用 Claude 精修**(仅该片段画面上云)。

**非目标(明确放到后面)**
- 人脸登记(「谁」这条事实有用,退成快速跟进的可选增强,不是 MVP 核心)。
- 意图捕捉(核心瞬间 / 为什么重要 / 主角)、可交互故事会话、自动选片重排。
- 片段内子段选择(先做整片选择)。
- 创作/剪辑本身(用户主导,在 story / render 层,后面)。

## 3. 用户体验

**清晰卡(每个 clip 一张):**
- 几张代表**关键帧**缩略图(一眼核对,防描述出错)。
- **1-3 句「这是什么」摘要**(谁 / 做什么 / 在哪,简短、能区分)。
- **关键事实**:拍摄时间、时长、地点(有 GPS 时)、来源文件名。
- 描述**来源标记**(本地 / Claude 精修)。
- 两个动作:**精修**(一键 Claude 重写这一条)、**选中**(加入工作集)。

**通览整库:** 清晰卡列表,默认**按拍摄时间排序、按天/场次分组**(素材自带拍摄时间,分组帮助通览)。补充:按时间 / 地点 / 已选 轻量筛选。

**界面(近期,无 GUI):**
- **看**:生成**静态 HTML 目录**(不建服务器,浏览器打开),卡片里的缩略图用 `<img>` 指向本地文件,卡片显示片段 id。
- **做**(选中 / 精修):**CLI 命令**,对着 HTML 里列出的 id 操作。
- 已知小摩擦:看在网页、操作在命令行,要对着 id。MVP 接受,以后网页双窗 UI 把两者合一(本层数据与动作不变,换前端即可)。

## 4. 架构与组件

新增包 `composerv/clarity/`,在现有 index / store / analyze 之上加一层「呈现 + 选择」。每个模块单一职责、接口清晰、可独立测试。

- `clarity/summarize.py`:把一个 clip 变成「清晰摘要」(`ClaritySummary`:text + source + facts)。本地默认,Claude 精修是同一接口换 runner。**与现有 `analyze/clip_video` 的逐帧 moments 分开**:moments 留给以后剪辑;这里只要「这是什么」的一句话级摘要。
- `clarity/keyframes.py`:为展示挑选少数代表帧(默认均匀几张;可后续升级为场景变化感知),输出缩略图文件路径。复用现有 `index/frames.sample_frames`。
- `clarity/catalog.py`:**纯函数** `render_catalog(cards) -> str`,把数据渲染成静态 HTML 字符串(分组、排序、缩略图、事实、id)。无副作用,易测。
- `store` 扩展:见 §5。
- CLI(`composerv/cli/main.py` 加子命令):`catalog`(生成 HTML)、`select` / `unselect`(改工作集)、`refine`(对单片段 Claude 精修描述)。

## 5. 数据模型(store 扩展)

时间码沿用「源媒体秒」。在现有 `Store`(SQLite WAL;已有 assets / captions / clip_summaries)上扩展。

每个 clip(asset)新增:
- `clarity_summary TEXT`:面向「这是什么」的简短摘要。**决定用独立字段,不复用 `clip_summaries`**:语义不同(`clip_summaries` 是整段/逐帧理解的产物,clarity 是面向用户秒懂的精炼描述),且需要独立的来源标记与精修生命周期。
- `clarity_source TEXT`:`local` | `claude`(描述来源/是否精修过)。
- `selected INTEGER`:0/1,是否在工作集。
- 关键帧缩略图:`keyframes` 小表或 JSON 列(clip_id, t, thumb_path),供卡片展示。

关键事实(时间/时长/地点)已经能从现有 `probe`/`MediaInfo` 得到(capture_time、duration、GPS 若有),无需新存,渲染时读取。

读写接口(示意):`set_clarity_summary(path, text, source)`、`get_clarity_card(path)`、`set_selected(path, bool)`、`list_selected()`、`set_keyframes(path, [(t, thumb)])`。

## 6. 数据流

已有:`scan_dir` → CFR 代理 `make_proxy` → 抽帧 `sample_frames` → 本地理解 → SQLite。

本层新增(在「本地理解」之后):
1. **清晰摘要**:`summarize_clip(proxy, facts, run=local_or_claude)`,提示词调成「描述这是什么:谁 / 做什么 / 在哪,1-3 句,简短能区分,不要逐帧罗列」。默认本地;`refine` 时换 Claude runner。写 `clarity_summary` + `clarity_source`。
2. **展示关键帧**:`pick_keyframes(proxy)` → 导出缩略图 → `set_keyframes`。
3. **目录渲染**:`composerv catalog [--group-by day]` → 读所有 clip 的(摘要 + 缩略图 + 事实 + selected)→ `render_catalog` → 写 `catalog.html`。
4. **选择**:`composerv select <id...>` / `unselect <id...>` → 改 `selected`。`list_selected` 是工作集,供后续创作读取(本层只负责持久化 + 可导出 id 列表)。
5. **精修**:`composerv refine <id>` → 用 Claude(`claude_read` 对该片段关键帧)重写 `clarity_summary`,`clarity_source=claude`。受敏感/同意约束(§7)。

## 7. 引擎与隐私(已定)

- 描述**默认全本地**生成(Qwen2.5-VL 本地;7B 当前默认。注:逐帧 moments 弱,但「这是什么」级摘要本地可用)。画面不出机器。
- **Claude 精修**是显式、单片段、按需的:仅当用户对某片段执行 `refine` 时,该片段的关键帧才上云(走 `claude_read`,用 Claude Code 订阅,无 API key)。
- 默认拦截敏感内容上云:后续接入 `meaning_overrides`/敏感标记后,`refine` 对敏感片段需显式同意。MVP 阶段至少在 `refine` 前明确告知「这一片段画面将上云」。

## 8. 错误处理 / 边界

- 本地理解失败 / 摘要为空 → 卡片显示「未生成描述」+ 仅靠关键帧 + 事实;不阻塞目录。
- 无 GPS / 无拍摄时间 → 对应事实留空,分组退化到按文件或「未知时间」。
- 关键帧抽取失败(损坏片段)→ 卡片用占位图 + 标记。
- `refine` 网络失败 → 保留原本地描述,提示失败,不破坏已有数据(沿用后端「错误哨兵、不写坏数据」的做法)。
- 幂等:重跑摘要/关键帧用「按片段路径覆盖」,不产生孤儿。

## 9. 单元划分与测试

- `clarity/catalog.py`(纯函数):给定若干卡片数据 → 断言 HTML 含每个片段的摘要、缩略图 `<img>`、id、分组结构。无 I/O。
- `clarity/summarize.py`:用**注入 runner**(canned JSON/文本)测摘要形状与来源标记;本地与 Claude 走同一路径,只换 runner。
- `clarity/keyframes.py`:用合成短片测「挑了 N 张、缩略图文件存在、时间戳记录」。
- `store` 扩展:`clarity_summary` / `selected` / `keyframes` 的写读往返;`list_selected` 正确。
- CLI:`catalog` 产出文件存在且含内容;`select`/`refine` 改对状态(`refine` 用 fake Claude runner)。
- 真实本地模型与真实 Claude 精修:用少量样片**手验**(像现有本地模型那样)。
- 全程遵循 TDD(先写失败测试)。

## 10. 复用现有代码

- `index/probe`(MediaInfo:capture_time / duration / GPS / 镜头信息)、`index/proxy`(CFR 代理)、`index/frames.sample_frames`、`index/scanner`。
- `store/db.Store`(扩展,不重写)。
- `analyze/clip_video`(本地摘要的底座之一)、`analyze/backends/qwen_mlx`(本地)、`analyze/backends/claude_cli.claude_read`(精修)、`analyze/backends/fake`(测试)。
- 不动 story / render/preview(本层不依赖它们;它们以后读「工作集」)。

## 11. 建议实现顺序(交给 writing-plans 细化)

1. store 扩展(clarity_summary / source / selected / keyframes)+ 测试。
2. `clarity/keyframes.py` + 测试。
3. `clarity/summarize.py`(本地默认,注入 runner)+ 测试。
4. `clarity/catalog.py`(纯函数 HTML)+ 测试。
5. CLI `catalog` / `select` / `unselect` + 测试。
6. CLI `refine`(Claude 精修,fake 测 + 真机手验)。
7. 端到端:对 `~/Movies/DJI_001` 取若干片段跑通 扫描 → 代理 → 本地摘要 → 关键帧 → 目录 → 选择 → 精修一条。

## 12. 验收

- 对一批真实 DJI 片段:运行后得到一个 `catalog.html`,每张卡有可信摘要 + 能核对的关键帧 + 时间/时长/地点;用户能 `select` 出工作集;对一条 `refine` 后描述明显更准且标记为 Claude。
- 用户读完目录后,能回答「我有哪些素材、各是什么」,而不需要逐个打开原片。
