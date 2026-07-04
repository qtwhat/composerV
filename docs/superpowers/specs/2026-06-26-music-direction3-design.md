# 音乐驱动剪辑(方向 3):设计

- 日期:2026-06-26
- 状态:设计稿,待用户审阅(brainstorming → spec → plan)。已过一轮对抗评审,修掉 2 个 blocker(渲染接线、卡拍与偏移顺序)+ 若干 important。
- 建在:`docs/superpowers/specs/2026-06-26-music-three-agent-collab-design.md`(方向 1+2,真选曲)之上,复用 `TrackFeatures.phrase_boundaries` / `climax_t` 预留字段、`MusicIntent`、`rank_tracks`、`beat_snap_segments`、`MontagePlan` 理由字段。
- 触发:真实成片验证后,用户发现「换了音乐但剪辑没变」—— 因为方向 1+2 只做「选曲」,剪辑由导演独立产出且锁在时间序。本轮让音乐反过来重塑剪辑。

## 0. 开工前置(写计划前必须先定)

- **分支**:方向 3 建在 `feat/music-three-agent-collab` 之上。二选一:(a) 先把那条合并进 main、方向 3 从 main 开新分支(干净,推荐);(b) 直接在那条上继续。定了再写计划。

## 1. 目标与范围

### 1.1 要达到
让**音乐结构反过来驱动剪辑**:分析曲子的高潮位置与乐句边界,让导演为情绪 / 音乐弧线**重排镜头**(打破严格时间序,把最强镜头送到音乐高潮),切点跟乐句走,并把音乐高潮**精确对齐**到最强镜头。

### 1.2 约束(用户定)
- **重排自由度**:保留**粗时间感**(下午→傍晚的推进还在),只为情绪 / 高潮做**局部重排**。不是纯 MV,仍带「重温这一天」的性质。
- **第二遍只编排,不增删镜头**:「留哪些」由第一遍情绪取舍说了算,音乐绑架不了取舍,只能重塑「怎么排」。

### 1.3 本轮不做(留后续)
- 音频动态塑形(音量包络、交叉淡出)——协商里导演提过,单独一轮。
- 用设计时三方 harness 去调「第二遍重排 prompt」——机制先建好,调优另开。

## 2. 两遍架构(数据流)

1. **第一遍(情绪剪,与现状相同)**:导演读素材表 → 只按情绪取舍 + 出 `MusicIntent`。`rank_tracks` 选曲。不受音乐影响。
2. **音乐结构分析**:对选中曲子算 `climax_t` + `phrase_boundaries`(第 3 节),已在 sidecar。
3. **第二遍(按音乐重排)**:把「第一遍已选镜头 + arc + 一个软性弧线引导」喂回导演,它在粗时间序内局部重排、标出最强镜头(`peak_clip_id`)、把它排到「弧线该到高潮」的位置、调时长(第 4 节)。**只重排,不增删。**
4. **确定性对齐(顺序很关键,先偏移后卡拍)**:
   - (a) 从第二遍排好的布局算最强镜头落在成片第几秒 `P`;
   - (b) `offset = climax_t − P`(带钳位,第 5.1 节)→ 写进 `MusicBed.start_offset_s`;
   - (c) 用**偏移后的网格**卡拍(切点优先吸乐句、其次节拍,第 5.2 节)。
5. `edit_to_intention` → 渲染(音乐从 `start_offset_s` 起播)。

## 3. 音乐结构分析(填 `climax_t` + `phrase_boundaries`)

近似方法(与 mode / valence 同性质,实现时拿 14 首真实曲核对):

- **`climax_t`**:RMS 能量包络,平滑约 2 秒窗去掉瞬时尖刺,取**持续能量最高点**的时间(不是单帧最响)。
- **`phrase_boundaries`**:节拍同步的 chroma + MFCC → 自相似矩阵 → novelty 曲线 → 峰值挑段落切换点。**兜底**:若 novelty 峰值数 < 3 或 > 15(判为不稳),改用固定乐句 = `4 * 60 / tempo_bpm` 秒(约 4 小节)从 0 起铺到曲子结束。

由 `compute_features` 算出,`composerv music index` 重跑写进 sidecar(source / license 保留)。

## 4. 第二遍导演(重排)

一次**轻量**导演调用,不重读全部素材。导演的活收窄成三件:**重排、标最强镜头、软性平衡弧线**。所有精确对齐(高潮压到镜头、切点卡乐句)都由第 5 节的确定性步骤做 —— 这样避开「导演还不知道成片多长、也不知道偏移」的鸡生蛋。

**输入**:
- 第一遍已选镜头(每个:clip_id、时长、这镜头是什么、reason)。
- 第一遍的 arc。
- **软性弧线引导**(不是精确秒 / 精确比例):「把情绪最强的镜头排到成片大约 60-70% 处,开头轻、结尾收;整体起—推—顶—收」。不喂乐句 / 高潮的精确位置(那些留给确定性步骤)。

**规则(prompt 写死)**:
1. 只重排 + 调时长,**不增删镜头**。
2. **保留粗时间序**:大体下午→傍晚,只为情绪 / 高潮局部重排。
3. 标出**情绪最强镜头** `peak_clip_id`,排到「弧线该到高潮」的位置。
4. **规则 2、3 冲突时,规则 2 优先**:若全局最强镜头在时间上很靠前 / 靠后、搬到高潮位就会破坏粗时间序,则**在时间序允许的高潮窗口内选一个次强的**当 `peak_clip_id`,并在 reason 里记下这个取舍。

**输出**:重排 + 调时长的镜头列表(同一批 clip_id,可改 in/out 与顺序)+ `peak_clip_id`。

**新增** `director/prompt.py`:`build_reorder_prompt` + `parse_reorder`(第一遍的 `build_director_prompt` 不动)。`parse_reorder` 容错:解析失败 → 退回第一遍原顺序、`peak_clip_id` 置空(见第 9 节)。

## 5. 确定性对齐(先偏移,后卡拍)

导演把镜头「排得对」,这一步「对齐得准」。**顺序**:先算音乐偏移,再用偏移后的网格卡拍(B2:两者不是独立步骤)。

### 5.1 高潮对齐 = 滑动音乐,不扭曲画面(`align_music_to_peak`)
- `P` = 第二遍布局里 `peak_clip_id` 那镜头的成片时刻(取起点或中点,定一个);成片总长 `reel_dur` = 各镜头时长和。
- `offset = climax_t − P`。钳位:
  - `offset < 0`(高潮镜头比曲子高潮还早)→ `offset = 0`,记「climax-earlier-than-peak」。
  - `offset > 0` 且 `duration_s − offset < reel_dur`(尾巴盖不满)→ `offset = max(0, duration_s − reel_dur)`,记「coverage-gap」;若此时音乐仍短于成片,让音乐提前结束(方向 1+2 现有行为)+ 记日志。
  - `climax_t` 很晚导致偏移后开头几乎无引子 → 是可接受的退化,live gate 评估(E4 证伪)。
- 写 `MusicBed.start_offset_s = offset`。返回 `offset`。
- `P` 用**卡拍前**的布局算;卡拍只做零点几秒的微移,`P` 漂移可忽略,偏移不必迭代。

### 5.2 乐句感知卡拍(扩展 `beat_snap_segments`,用偏移后的网格)
- 音乐从 `start_offset_s` 起播,所以对齐用**偏移后**的网格:`phrases_reel = [p − offset for p in phrase_boundaries if p ≥ offset]`,`beats_reel = [b − offset for b in beat_times if b ≥ offset]`。
- 每个切点先找最近的**乐句边界**(较大容差),够近吸到乐句;够不着退回吸到最近**节拍**(现有逻辑)。大结构切点落乐句,细切点仍卡拍。

**落产物**:`MontagePlan` / 成片旁 sidecar 记下重排后的顺序、`start_offset_s`、对齐后的高潮镜头、乐句卡拍点、以及 5.1 记的日志标签。

## 6. 数据模型改动

- `models.py` `MusicBed` 加 `start_offset_s: float = 0.0`(音乐从曲子的这个秒数起播;渲染时生效)。默认 0.0,老的 rationale JSON 缺这个键会由 pydantic 用默认值补上,向后兼容。
- `TrackFeatures.climax_t` / `phrase_boundaries`:本轮开始被 `compute_features` 填充(方向 1+2 里预留、留空)。
- `MontagePlan`:复用 / 扩展理由字段,记重排 + 偏移 + 对齐 + 乐句卡拍。

## 7. 代码落点

- `composerv/music/features.py`:`compute_features` 填 `climax_t` + `phrase_boundaries`(新 `_estimate_climax`、`_phrase_boundaries`);重跑 index 写 14 首 sidecar。
- `composerv/models.py`:`MusicBed.start_offset_s`。
- `composerv/director/prompt.py`:`build_reorder_prompt` + `parse_reorder`。
- `composerv/director/montage.py`(主集成点):重构成 `build_director_montage`(第一遍,保持现签名 + 现有测试不动)+ 一个两遍封装(先调第一遍,再结构分析、第二遍重排、`align_music_to_peak`、偏移后卡拍)。集成测试打两遍封装。
- `composerv/music/beatsnap.py`:卡拍改「优先乐句、其次节拍」,接收**偏移后**的网格;新增 `align_music_to_peak(segments, climax_t, peak_idx, reel_dur, track_dur) -> offset`(确定性 + 钳位)。
- **渲染接线(B1,必须做,否则偏移是空操作)**:
  - `composerv/render/preview/edl.py`:序列化 `edl["music"]` 时,当 `start_offset_s != 0` 加上 `"start_offset_s"` 键。
  - `composerv/render/preview/composition.py` `_add_music_track`:把音乐源范围从 `CMTimeRangeMake(kCMTimeZero, use)` 改成从 `CMTimeMakeWithSeconds(offset, ts)` 起、长度 `use = min(src_dur − offset, total)`。
  - `composerv/render/export.py`:走 `build_composition` → 自动继承上面的改动;加一行注释确认,不用另改。
- `MontagePlan` / rationale:记新字段。

## 8. 测试策略

- **纯函数 TDD**:`_estimate_climax`、`_phrase_boundaries`(gated = 除非设了环境变量才跑,沿用 `beat.py` 的 `CV_RUN_SLOW` 约定,拿 14 首真实曲核对 climax 落高潮处、边界落段落切换处)、乐句优先卡拍(用偏移后网格)、`align_music_to_peak`(各钳位分支)、`parse_reorder`。
- **集成**:两遍 montage 在测试成片上(缓存感知 + 注入 LLM 两次),断言重排 + `start_offset_s` + 偏移后乐句卡拍都记进 MontagePlan。
- **渲染**:`MusicBed.start_offset_s` 起播的 composition / export 测试(断言音乐源范围从 offset 起)。
- **真片 live gate(人工节点)**:渲一条方向 3 的测试成片,跟方向 1+2 那条对比 —— 高潮镜头是否落到音乐高潮、切点是否跟乐句、粗时间感是否还在。用户看+听拍板。

## 9. 错误处理

- 曲子无 `climax_t` / `phrase_boundaries`(旧 sidecar 未含)→ 退回方向 1+2 行为(卡拍走节拍、不重排、offset=0)。
- 第二遍导演解析失败 / 超时 → 退回第一遍时间序编辑,**且跳过高潮对齐**(offset=0、无 `peak_clip_id`,记「no-peak-from-pass2」),乐句卡拍用**未偏移**网格。不崩。
- 高潮对不齐(偏移越界)→ 按 5.1 钳位 + 记日志。
- 重排破坏粗时间序 → 由 prompt 规则 2/4 约束;若仍越界,记日志(下一轮收紧)。

## 10. 决策日志

每条:决定 / 理由 / 否决的替代 / 证伪条件。

- **E1 两遍架构(方案 B),非音乐先定 / 非确定性重排**
  - 理由:「情绪取舍」(第一遍,音乐无关)与「音乐编排」(第二遍)干净分开;取舍不被音乐绑架,重排交给导演判断。
  - 否决:A 音乐先定(预选曲子带偏取舍)、C 确定性重排(规则排得别扭,失去编辑判断)。
  - 证伪:若第二遍重排在 live gate 上经常比第一遍时间序更差(用户否),两遍重排价值不成立。
- **E2 第二遍只重排 / 调时长,不增删**
  - 理由:保住「留哪些=情绪说了算」的分离。
  - 否决:允许第二遍增删(音乐又能绑架取舍)。
  - 证伪:若为让弧线成立,导演频繁需要增删,则「只重排」太紧。
- **E3 保留粗时间序,局部重排(非纯 MV),冲突时时间序优先**
  - 理由:保住「按顺序重温这一天」的记忆 reel 性质。
  - 否决:完全自由重排(纯 MV,丢记忆性质)、彻底按段落重分桶。
  - 证伪:若粗时间序经常挡住好的高潮对齐(最强镜头总在很靠前),约束与目标冲突,需重议(规则 4 会退而求次强,但若次强也弱,成片高潮就弱)。
- **E4 高潮对齐用音乐偏移(滑动音乐),不扭曲画面**
  - 理由:节奏 / 时长是导演领域,不该被确定性步骤扭曲;滑动音乐无损画面。顺带实现「给长曲子挑一段」。
  - 否决:拉伸 / 压缩画面时长凑高潮(扭曲导演排好的节奏)。
  - 证伪:若曲子普遍没足够 runway 偏移(太短 / 高潮太靠前 / 太靠后),偏移法经常钳到 0 或退化,需回退到画面微调、或让选曲阶段就把 runway 纳入打分。
- **E5 climax = 平滑能量峰;phrase = 结构分析边界(近似)**
  - 理由:librosa 可给可用近似,本地优先。
  - 否决:接外部 MIR 服务(违背本地优先)。
  - 证伪:若 climax / phrase 在真实曲上与人耳常不符,对齐就对到错的点。
- **E6 导演只做重排 + 标高潮 + 软性弧线引导,所有精确对齐交确定性步骤**
  - 理由:避开「导演不知道成片多长、也不知偏移」的鸡生蛋;导演擅长「大致排到 60-70%」,不擅长凑精确秒;把偏移 + 乐句卡拍放确定性步骤,可测、可回溯,顺序也能保证(先偏移后卡拍)。
  - 否决:把精确乐句 / 高潮秒喂给导演让它自己对齐(它没有总长、没有偏移,对不准)。
  - 证伪:若导演按软引导排后,高潮镜头位置仍常偏离到偏移钳位失效,需改成两阶段(先定长度再喂绝对时间)。

## 11. 范围外 / 风险

- 范围外:音频动态塑形(音量包络 / 交叉淡出);用 collab harness 调重排 prompt。
- 风险:粗时间序 vs 高潮对齐冲突(E3);音乐偏移 runway 不足(E4);climax / phrase 估计准确度(E5);两遍 = 每条成片两次 Opus 调用(成本)。
