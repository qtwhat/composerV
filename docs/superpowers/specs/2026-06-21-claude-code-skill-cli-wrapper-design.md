# composerV:对话界面 = Claude Code skill 包住现有 CLI — 设计 + 需求 spec

- 日期:2026-06-21
- 状态:方案已选定(用户拍板「skill 包住现有 CLI，最快」),待写实现计划
- 关联:[[clip-clarity-selection-design]](2026-06-19-clip-clarity-selection-design.md);总体三层架构 `~/.claude/plans/...crystalline-panda.md`

## 1. 背景与决定

研究了 ChatCut(浏览器内、云渲染的文字稿编辑器,核心交互是对话式 agent + 文字稿即时间线)后,用户的结论:**对话界面要保留,且不想脱离 Claude Code(CC),继续基于 CC 构建。**

把「建在 CC 上」拆成两层看(ChatCut 本身也是这么分的):

| 层 | 职责 | 谁承担 |
|---|---|---|
| **命令层** | 用自然语言说意图,路由到正确的操作并执行 | **CC 对话 + 本 skill** |
| **画布层** | 看片、选片、看回放 | **HTML 目录 + 原生预览窗口**(CC 文本窗渲染不了) |

CC 适合承担命令层(对话循环、工具调用、LLM 推理都是现成的);画布层 CC 给不了,由静态 HTML(`catalog.html` / `people.html`,浏览器打开)和原生 AVFoundation 预览窗口(独立进程)承担。两者并存,不是「离开 CC」。

**选定方案:写一个 Claude Code skill,薄薄地包住现有 `composerv` CLI。** 不是 MCP(那是后续升级),不重写任何逻辑。原因:最快;现有 CLI 已经能跑通端到端;先用它验证对话交互的手感,再决定要不要升级 MCP。

## 2. 核心架构原则(防锁死)

**所有逻辑留在 Python 库 + CLI 里。skill 只是一个薄前端,把自然语言翻译成 CLI 调用,再把结果/产物(HTML 路径、工作集、预览窗)交还给用户。**

这样 CC 是前端 #1;将来做 web 双窗口时它是前端 #2,调的是同一套 core,core 一行不用改。「建在 CC 上」因此不损失任何选择权:赌的是自己的 core,不是 CC。现有结构(`composerv` 是 Python 包,`pyproject` 暴露 `composerv` / `composerv-preview` 两个入口)已经是这个形状,保持住即可。

## 3. 目标 / 非目标

**目标**
1. 用户在 CC 里用自然语言,就能驱动现有 CLI 的全部命令,**不必记确切的子命令名和 flag**。
2. skill 在合适时机替用户**打开画布**:目录 HTML、人脸联系表 HTML、原生预览窗口。
3. 守住隐私默认:**本地优先**;只有用户明确要求时才走云端(`refine` / `catalog --cloud`),且**上云前明确告知哪一部分画面会上传**。

**非目标(明确放到后面)**
- MCP server(本 skill 验证手感后再升级;接口契约见 §8)。
- 在 CC 文本窗内做任何视觉渲染 / 视频回放(那是画布层的事)。
- 给现有 CLI **新增**子命令(本 skill 只包已存在的;`story` / `storylines` / `montage` / fcpxml 导出尚无 CLI,见 §7 的缺口处理)。
- 改任何 Python 逻辑(纯前端工作)。

## 4. 需求(功能)

skill 必须满足:

- **R1 不重写逻辑**:skill 通过运行 `composerv <subcommand> ...`(和 `composerv-preview`)完成一切,绝不在 skill 内复制业务逻辑。
- **R2 意图映射**:把用户口语意图映射到现有命令(映射表见 §6),flag 由 skill 补全,用户不必知道。
- **R3 一致的路径约定**:`--db`(默认 `composerv.db`)与 `--work-dir`(默认 `.composerv`)在一次会话内保持一致;skill 须在会话里记住当前用的 db / work-dir / 目录,后续命令复用,不让用户每条重报。
- **R4 打开画布**:`catalog` / `faces` 跑完后,skill 给出 HTML 绝对路径并(在用户同意下)用浏览器打开;`preview` 直接拉起 GUI 窗口,`--check` 模式则只回报时长 + 重建延迟。
- **R5 隐私闸**:`refine` 和 `catalog --cloud` 是仅有的两条会把画面上云的路径。skill 在执行前必须明确告诉用户「这一(或这一批)片段的画面将上传到 Claude」,默认其余一切本地。敏感人物(`name --sensitive` 标记过的)按 store 的敏感闸约束。
- **R6 结果回读**:命令产出(选了几条、工作集多少、目录多少卡、精修后的新描述)由 skill 用自然语言转述回给用户,而不是只丢一行 stdout。
- **R7 缺口诚实**:对尚无 CLI 的能力(故事线分析、montage、fcpxml 导出),skill **不假装能一键做**;按 §7 处理。

## 5. 需求(非功能 / 环境约束)

均来自现有项目事实,skill 必须遵守:

- **N1 代理**:本机任何 `claude` / HF 网络调用需 `CV_CLAUDE_PROXY=http://127.0.0.1:7897`。
- **N2 Claude 走订阅,非 API key**:云端描述/精修通过 `claude -p --allowedTools Read`(Claude Code 订阅,只读,**不能 bypass**,bypass 被安全分类器拦)。
- **N3 本地模型离线**:本地理解(Qwen2.5-VL,默认 7B)跑 `HF_HUB_OFFLINE=1`;本地描述每片段重载模型较慢(数十秒级),skill 转述时应让用户对耗时有预期。
- **N4 不阻塞**:长命令(批量 ingest、本地理解)skill 应说明这是长任务,不让用户以为卡死。
- **N5 ffmpeg/ffprobe** 走 Homebrew;无 `drawtext` 滤镜(合成片用 `testsrc`)。

## 6. 意图 → 命令 映射表(skill 的核心知识)

| 用户自然语言(示例) | skill 执行 |
|---|---|
| 「把这个文件夹的素材整理出来看看 / 建个目录」 | `composerv catalog <dir> --db <db> --out <out> --work-dir <wd>`,然后给出并打开 `<out>` |
| 「重新生成目录(不重新理解)」 | `composerv catalog --db <db> --out <out>`(不带目录参数) |
| 「只看前 N 条 / 先跑一小批」 | `composerv catalog <dir> --limit N ...` |
| 「这条看不清,用 Claude 再描述一遍」 | (先告知该片段画面上云)`composerv refine <clip_id> --db <db>` |
| 「整批用云端描述」 | (先告知上云范围)`composerv catalog <dir> --cloud ...` |
| 「选 X、Y、Z / 这几条加入工作集」 | `composerv select <ids...> --db <db>` |
| 「把 X 取消」 | `composerv unselect <ids...> --db <db>` |
| 「我选了哪些 / 工作集里有什么」 | `composerv selected --db <db>` |
| 「素材里有谁 / 看人脸」 | `composerv faces --db <db> --out people.html`,打开 HTML |
| 「把 3 号叫『妈妈』」 | `composerv name <id> <name> --db <db>`(逝者等加 `--sensitive`) |
| 「2 号和 5 号是同一个人」 | `composerv merge <into> <ids...> --db <db>` |
| 「预览这个剪辑 / 放一下」 | `composerv preview <edl.json>`(GUI);要测时长/延迟用 `--check` |
| 「composerV 什么版本」 | `composerv version` |

## 7. 缺口处理(尚无 CLI 的能力)

故事线分析(`story/storylines.analyze_storylines`)、beatfill、montage(`music/`)、fcpxml 导出(`render/fcpxml`)目前**只能用 Python 跑,没有 CLI 子命令**。处理原则:

- 短期:skill 对这些能力**明说「还没有 CLI 入口」**,不硬凑;若用户坚持,skill 可运行一段最小 Python 片段(`uv run python -c ...`)兜底,并标注这是临时做法。
- 正路:把它们补成 CLI 子命令(`composerv story` / `composerv montage` / `composerv export` 等,见 [[composerv-status]] 的 TODO),补一个就在 §6 映射表加一行。**skill 的能力边界 = CLI 的能力边界**,这正是「逻辑留在 core」原则的好处。

## 8. 升级到 MCP 的接口契约(为后续留口,本期不做)

本期 skill 验证完手感后,自然升级为 MCP server。为不返工,现在就把每条命令当成将来的一个 MCP 工具来想:

- 每个 CLI 子命令 ↔ 一个 MCP 工具;命令的参数 ↔ 工具的类型化入参。
- 工具应回结构化结果(选中数、工作集、卡片数、HTML 路径、精修后的 `ClaritySummary`),而非纯文本 stdout。本期 skill 已按 R6 做「转述」,届时把转述换成结构化返回即可。
- db / work-dir 这类会话级状态(R3),MCP 期改为 server 的会话上下文。

## 9. skill 文件落位(实现时)

Claude Code 项目级 skill 放 `.claude/skills/composerv/SKILL.md`。SKILL.md 内容 = §2 原则 + §4-§7 的需求与映射表的可执行化(描述触发词、命令模板、隐私话术、缺口话术)。本 spec 是它的需求来源。

## 10. 验收

- 用户在 CC 里全程用自然语言,不报任何 flag,即可:对一个真实文件夹建目录并在浏览器看到 → 选出工作集 → 对一条 `refine`(并在上云前被告知)→ 看人脸联系表并给一个人命名 → 预览一个 EDL。
- 全程未在 skill 内出现任何被复制的业务逻辑(R1):删掉 skill,同样的命令在终端手敲一遍能得到完全一致的结果。
- 对尚无 CLI 的能力,skill 诚实说明,不误导(R7)。

## 11. 建议实现顺序(交给 writing-plans 细化)

1. 写 `.claude/skills/composerv/SKILL.md`:触发词 + §6 映射表 + 隐私话术 + 缺口话术 + 路径约定(R3)。
2. 在真实素材上手验 §10 的端到端一条龙。
3. 把高频缺口补成 CLI 子命令(优先 `story` / `montage`),每补一个回写 §6。
4. (后续)按 §8 升级 MCP。
