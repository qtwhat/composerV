# composerV

[English](README.md) · **中文**

[![在线 demo](https://img.shields.io/badge/▶_在线_demo-qtwhat.github.io%2FcomposerV-D97757)](https://qtwhat.github.io/composerV/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB)
![Platform: macOS · Apple Silicon](https://img.shields.io/badge/platform-macOS_·_Apple_Silicon-lightgrey)

> **▶ 在线看可交互的管线总览：**[qtwhat.github.io/composerV](https://qtwhat.github.io/composerV/)，点任一阶段看它的内部结构。仓库里的 `index.html` 是源码，点上面链接看渲染后的页面。

一个本地优先、以故事为先的助手，把大体量的个人视频档案（GoPro / 手机，几百 GB）
整理成一个你自己认可的故事，再交给 Final Cut Pro 做后期收尾。

难而有价值的部分是帮你想清楚这个故事，不是搜索，也不是格式转换。故事层以下的
一切，都是为它服务的。

## 架构总览

![composerV 管线：摄取 → 分析 → 确认 → 导演 → 渲染，全部围绕一个中央 SQLite store](docs/architecture.zh-CN.png)

五个阶段连成一条线，全部围绕一个中央 SQLite **store**（`composerv.db`）运转。慢的
感知工作（VLM 看画面、Whisper 听声音、on-device 审美打分）**只跑一次并写入
store**；导演每次**只读这份缓存**，所以出片和改片都快。这张图的可交互版本（点某个
阶段看它的内部结构）在线地址是
**[qtwhat.github.io/composerV](https://qtwhat.github.io/composerV/)**（源码：[`index.html`](index.html)）。

## 一句话概括

把视频变成**语义 + 元数据**，让 LLM 帮你*策展*（去重、控制节奏、增加变化、按叙事
需要挑选），而不只是按属性匹配。你写故事主线，AI 填充每一拍；**零渲染实时预览**
即时反映每一次改动；锁定的故事编译成 FCPXML 交给 Final Cut。

## 三层架构

- **`index/`**（底层）：扫描 → CFR 720p 代理片 + 抽帧（带真实源 PTS）→ VLM 描述 /
  物体 / OCR / 景别 / WhisperX 字幕 / insightface 人脸 / GPS+时间 / 情绪+质量信号
  → SQLite + sqlite-vec + 每片段 sidecar → 一份 LLM 能推理的三层 Archive Brief。
- **`story/`**（产品本体）：人来写主线（核心立意 + 目标情绪）；AI 提出结构，用按
  叙事重要性（不是精彩程度）排序的候选片段填充每一拍；`compile(Story) →
  IntentionList`。
- **`render/`**（输出）：一份 IntentionList，三个目标：实时 **AVComposition** 预览
  （零渲染）、手写的 **FCPXML 1.13** 输出器（给 Final Cut）、以及 storyboard /
  可选的压平分享版。

## 管线阶段与进展

五个阶段连成一条线，`catalog → analyze → confirm → montage → preview`，每个都是一个
CLI 子命令，读写共享的 store：

| 阶段 | 命令 | 状态 | 做什么 |
|---|---|---|---|
| ① 摄取 | `composerv catalog` | ✅ 已实现 | 扫描文件夹 → CFR 720p 代理片 + 关键帧 → 描述 / OCR / 景别 → store |
| ② 分析 | `composerv analyze` | ✅ 已实现 | 逐帧 VLM moments + WhisperX 字幕 + on-device 审美；**慢，只跑一次，写缓存** |
| &nbsp;&nbsp;└ 审美打分 | *（analyze 内部）* | ✅ 已实现 | Apple Vision 本地质量 / 情绪打分；只影响 in 点，不影响选镜头 |
| ③ 确认 | `composerv confirm` | ✅ 已实现 | 人像命名 + 一段用户 brief → `persons` / `briefs`；brief 作为最高优先级的人类指引注入 |
| ④ 导演 | `composerv montage` | ✅ 已实现 | 对一张文字 **footage table** 做一次 Claude 调用 → 剪辑决策 + 配乐意图 |
| ⑤ 渲染 | `composerv preview` / `export` | ✅ 已实现 | 零渲染 AVComposition 预览 · FCPXML 1.13 · storyboard / MP4 |
| &nbsp;&nbsp;└ 自动重构图 | *（render 内部）* | ✅ 已实现 | 把竖拍片段裁剪填满 16:9，跟随主体；旋转过的素材摆正 |
| 音乐驱动剪辑 | *（方向 3）* | 🔷 规划中 | 两遍重排，让音乐高潮落在最强的镜头上；spec 已评审，10 个任务已规划，未开工 |

配乐选择本身已经能用：导演给出 `MusicIntent`，确定性的 `rank_tracks` 选中曲子，
片段按节拍网格卡拍。

## 工具链

Python 3.12，用 [uv](https://docs.astral.sh/uv/) 管理。重的 ML / 平台依赖是可选
extras（`analyze-local`、`analyze-api`、`transcribe`、`faces`、`preview`、`vector`），
所以核心装起来很轻。

```sh
uv sync                 # 核心 + 开发依赖
uv run pytest           # 跑测试
```

## 不用自己的素材也能试

`composerv demo` 会生成一套全合成的演示素材（测试图样片段，带合成语音和一块可 OCR
的路牌，外加两首带节拍网格、能量弧不同的音乐），零下载、零许可、不碰任何个人媒体：

```sh
uv run composerv demo ./composerv-demo
# 然后按打印出来的 catalog / music index / analyze / montage 命令走
```

导演（montage）这一步需要 Claude：要么用 `claude` CLI（Claude Code 订阅，装了就
自动用），要么用 Anthropic API key（`uv sync --extra analyze-api`，然后
`export ANTHROPIC_API_KEY=...`）。两者都有时，设 `CV_CLAUDE_BACKEND=api` 强制走 API。
感知（analyze）完全本地运行，两个都不需要。

## 环境变量配置

| 变量 | 默认值 | 含义 |
|---|---|---|
| `CV_OUT` | `~/Movies/composerV` | 输出根目录（成片、EDL/FCPXML/storyboard）以及默认 DB 位置 |
| `CV_MUSIC_DIR` | `~/.composerv/music` | 按 `<feeling>/` 分类的音乐库 |
| `CV_AESTHETICS_BIN` | 自动编译 | 编译好的 Swift 审美打分器路径 |
| `CV_CLAUDE_BACKEND` | 装了 CLI 就用 CLI | 设 `api` 强制用 Anthropic API 而非 `claude` CLI |
| `CV_CLAUDE_PROXY` | 无 | `claude` CLI 调用走的 HTTP(S) 代理 |

## 许可证

MIT（见 `LICENSE`）。运行时下载的 ML 模型有各自的许可证：特别是 insightface 人脸
模型**仅限非商业研究使用**，商用前请看 `THIRD_PARTY_NOTICES.md`。
