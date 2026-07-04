# composerV:动态压低 — 重要现场声(如孩子说话)时压低音乐、顶起人声

- 日期:2026-06-21
- 状态:设计已核查 + 用户已定取舍,按 TDD 实现中
- 来源:deep-research/设计 workflow(5 角度并行 + 对抗式核查;`wf_5ad3bf98-2c7`)
- 关联:[music muxing 改动](../../../composerv/models.py)(MusicBed);[skill 决定](2026-06-21-claude-code-skill-cli-wrapper-design.md)

## 1. 需求

平时音乐为主(已实现:音乐 0dB、原声压低 -15dB)。新增:在「重要现场声」窗口内(孩子说话、笑声),**把音乐再压深、把那段原声顶到前面**,过后恢复音乐。即动态/侧链压低,是现有「固定音乐为主」的反向。

## 2. 行为(两种状态)

任意时刻只有两种状态:
- **平时**:音乐 = `gain_db`(0dB);原声 = `duck_db`(-15dB)。
- **高亮**(在一个窗口 + 短保持内):音乐 = `music_duck_db`(-18dB);原声 = `highlight_db`(0dB)。窗口两端各 `ramp_s`(0.25s)线性渐变,避免咔哒/抽水声(pumping)。

两个音乐电平(平时 `gain_db` / 窗口内 `music_duck_db`)、两个原声电平(平时 `duck_db` / 窗口内 `highlight_db`)。

## 3. 用户已定的三个取舍

1. **窗口坐标 = 源片秒 + 编译时投影**(不是只存时间线秒)。检测产出的窗口存在片段源时间上,**重排/改剪辑后依然有效**;渲染前一个薄适配层(纯函数,非模型)把每片段的源窗口投影成时间线坐标、裁到该片段 `[in_sec,out_sec]`、合并重叠、丢掉小于 2 帧的窗口。投影后写入 `MusicBed.highlights`(时间线秒),它仍是 preview 与 FCPXML 共读的唯一契约。
2. **FCPXML 先整片静态级**:命中窗口的片段整片导成 `highlight_db`、音乐导成 `music_duck_db`(无片段内渐变);**preview 走完整动态**(高保真路径)。关键帧版已写好但 `xfail(strict)` 门控,等用户从 FCP 导一个带音量关键帧的样本锁定属性名/单位后再开。
3. **检测 = 机制 + 通用语音(silero VAD)**:先建压低机制(手动标 + 本地 VAD 自动找说话段),任何人声触发高亮。「specifically 这个孩子」靠声纹登记,是后续可选项(见 §6)。

## 4. 契约改动(models.py / edl.py)

```python
class AudioHighlight(BaseModel):
    start_s: float            # 时间线秒(与 emitter 的累计 offset、preview 游标同轴)
    end_s: float              # 时间线秒;校验 end_s > start_s
    ramp_s: float = 0.25      # 两端各渐变
    music_duck_db: float | None = None  # None -> 用 MusicBed.music_duck_db
    clip_db: float | None = None        # None -> 用 MusicBed.highlight_db
    label: str = ""           # 如 "child speaks";带进 FCP marker
```
`MusicBed` 扩展(全部带默认,`MusicBed(path=...)` 不变,向后兼容):
```python
    highlights: list[AudioHighlight] = Field(default_factory=list)
    music_duck_db: float = -18.0   # 窗口内音乐压到的更深电平
    highlight_db: float = 0.0      # 窗口内原声顶起的电平
```
现有字段保留原义(平时态):`gain_db`=窗口外音乐、`duck_db`(-15)=窗口外原声、`fade_out_s`=结尾淡出。

**EDL 序列化(关键的向后兼容)**:窗口存在现有 `edl["music"]` 字典内,**`highlights==[]` 时必须省略**这些键(条件发射,沿用 `if il.music is not None` 的写法),不能发空列表 —— 否则 `tests/test_preview_edl.py` 第 47/73 行对 4 键字典的精确相等断言会挂。`load_edl_file` 原样返回 `music`,消费方读 `music.get("highlights", [])`。

## 5. 三处实现 + 核查出的硬约束

**投影(纯函数,无 AVFoundation,可单测)**:按 `round(s*fps)/fps` 逐片段累加时长(与 `build_composition` 的逐片段帧取整一致),不能用原始 float 秒,否则非帧对齐片段边界漂移毫秒级。

**preview(composition.build_audio_mix)**:对同一个 `AVMutableAudioMixInputParameters` 发多段不重叠 `setVolumeRampFromStartVolume_toEndVolume_timeRange_`(本机已验证:多段叠加可读回;`setVolume_atTime_` 作基线锚)。每窗口:原声 `duck_db->highlight_db->duck_db`,音乐 `gain_db->music_duck_db->gain_db`,结尾淡出仍是最后一段。
- **硬约束 1(否则崩溃)**:重叠的 ramp 时间区间会抛 `NSInvalidArgumentException`(不是杂音,是崩)。投影层必须按「含边缘的区间 `[start-ramp, end+ramp]`」用半开区间合并(边界相接合法)。
- **硬约束 2**:窗口短于 `2*ramp` 时夹紧两端渐变避免相撞。恢复目标是 `linear(gain_db)`(可能非 1.0)。ramp 在线性幅度上插值,保持 0.25s 短。
- 无高亮路径必须走**同一分支**(别把固定压低改写成整长 flat ramp,会破坏 `isEqual:`)。

**FCPXML(emitter)**:整片静态 `<adjust-volume amount>` 路径先上。DTD 已逐字确认结构 `<adjust-volume>(param*)`,且 asset-clip 最多一个 adjust-volume。**未核实**:`<param name="...">` 的确切串、keyframe value 是裸 dB / "-18dB" / 线性 —— 故关键帧版 `xfail(strict)` 门控,等 golden FCP 导出锁定。
- **顺带修一个已存在的 bug**:当 clip 同时有 label 和音乐铺底,子元素顺序(adjust-volume, marker, 连接片段)不符合 1.13 DTD;正确顺序是连接片段在 marker 前。加 lxml 的 DTD 校验测试(dev 依赖加 lxml)。

## 6. 检测(只产生窗口,不进契约)

- **Phase 1(手动 + RMS,无新重依赖)**:手动写窗口的入口 + librosa RMS/能量基线(同时作测试 fake)。
- **Phase 1b(通用语音,无新重依赖)**:silero VAD 走 onnxruntime(仓库已有 onnxruntime/librosa/soundfile)。**不要装 pip 包 `silero-vad`**(它拉 torch)。改为放入 ~2.2MB `silero_vad.onnx` + 手写 numpy 推理(已验证:输入 input/state/sr,state float32 [2,batch,128],512 样本 16kHz 块,零 torch),像 `music/beat.py` 验证合成节拍那样,用合成「语音盖静音」WAV 现场验证。
- **Phase 2(可选,「这个孩子」)**:声纹登记(ECAPA-TDNN 192 维嵌入,新增本地可选依赖),复用 `faces/cluster.py` 的 `OnlineFaceClusterer` 余弦聚类,但用**独立的声音 gallery/clusterer 实例**(192 维不能塞进 512 维的 persons.centroid 列)。像给人脸命名那样给声音命名。**脸≠声音;4-5 岁童声靠年龄分类不可靠,只能靠登记**。

## 7. 默认值

`music_duck_db=-18`(对话压低 -12..-18 的前端,人声明确为主)、`highlight_db=0`、`duck_db=-15`(不变)、`ramp_s=0.25`、最小窗口 ≥ 2 帧。silero 迟滞:最短语音 ~250ms、最短静音 ~100ms、两侧 pad ~150ms + RMS 门去近静音误报。

## 8. TDD 顺序(核查给出)

1. models:AudioHighlight + MusicBed 新字段 + 默认;`MusicBed(path=...)` 仍可构造、值不变;`end_s>start_s` 校验。
2. edl:`highlights==[]` 时 `edl["music"]` 仍是原 4 键(老测试绿);非空时新键出现在 `edl["music"]` 内且 `load_edl_file` 往返。
3. 投影纯函数:clips+gap → clip2 时间线起点 = 前面片段时长和(逐片段帧取整);源窗口映射正确;含边缘重叠合并(半开)、<2 帧丢弃。
4. composition.build_audio_mix 单窗口:读回(`getVolumeRampForTime_...`)断言 t=0 原声=linear(duck_db)、音乐=linear(gain_db);窗口中段原声=linear(highlight_db)、音乐=linear(music_duck_db);退出渐变后回基线且结尾淡出仍把音乐收到 0。(现有 2 个 composition 测试只数轨/参数、不查音量,故新增读回断言。)
5. composition 重叠保护:两窗口边缘相撞**不得**抛异常(断言不抛 + 合并正确);无高亮路径产出与今天 `isEqual:` 相等。
6. emitter 已存在 bug 修复:加 lxml DTD 校验(DTD 缺失则 skip);今天对 music+label 片段失败,重排子元素为(adjust-volume, 连接片段, marker)。
7. emitter 整片静态兜底:命中窗口的片段静态 amount=highlight_db、音乐连接片段 amount=music_duck_db;无高亮输出与今天逐字节一致。
8. emitter 关键帧版(门控):断言 keyframeAnimation/keyframe 数 + 源局部 vs 时间线局部的时间分裂;value 格式断言 `xfail(strict)`,golden 导出锁定后翻成硬断言。
9. 检测(纯):概率数组的窗口合并/迟滞;RMS 基线在合成 WAV 上找回已知爆发;silero onnx 循环现场验;编排用 fake 检测器(`faces/enroll.py` 注入式)。

## 9. 验收

- 给手动窗口:preview 里平时音乐、窗口内音乐压深+原声顶起、两端平滑、结尾淡出;无窗口时与今天完全一致。
- FCPXML:命中片段整片人声优先、音乐压低,DTD 校验通过;无窗口时逐字节不变。
- silero 在真实素材上能自动标出说话段;手动可增删。
- 全程 TDD,新读回断言补上今天缺失的音量守护。
