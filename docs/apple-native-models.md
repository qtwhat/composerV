# Apple 原生模型能否满足 COMPOSERV 的感知/处理需求(调研)

> 2026-06-23。一次深度调研 + 对抗式验证(25 条可证伪声明全部 3-0 通过,主要用 Apple 一手来源)。回答:Apple 自己的端上框架(Vision、Foundation Models、Speech)能不能替代 COMPOSERV 现在的感知链路(Qwen2.5-VL + Whisper + insightface + Claude 导演)。
> 时效提醒:Apple 的能力每年 WWDC 都在变,版本门槛也重要,引用前先对当前文档核实。

## 一个前提:接入成本低

COMPOSERV 的渲染层已经通过 PyObjC 桥接 Apple 框架(AVFoundation / Quartz / CoreMedia)。所以加 Vision 只要装 `pyobjc-framework-Vision`,桥接模式现成。唯一例外是 Foundation Models(Swift-only),从 Python 调要写个 Swift 小程序。

## Apple 能替代或做得更好的:逐帧机械感知(端上)

| 需求 | Apple 原生 | 可用性 / 说明 |
|---|---|---|
| OCR | Vision `RecognizeTextRequest` | macOS 15+,18 种语言含中文;比 VLM-OCR 好:不幻觉、毫秒级、带框 |
| 镜头美学 / 画质 | `CalculateImageAestheticsScoresRequest` | iOS 18 / macOS 15(WWDC24);`overallScore` −1~1 + `isUtility`。COMPOSERV 已采纳 |
| saliency → reframe / Ken Burns 焦点 | attention + objectness 两个 saliency 请求 | iOS 13+ / Swift API macOS 15;正是 Apple 自动裁图用的技术 |
| 主体抠像 | `GenerateForegroundInstanceMaskRequest` | iOS 17/18;WWDC26 加了 tap-to-segment |
| 归一化目标框 | Vision 原生归一化坐标 | 人 / 脸 / 动物 / 条码 + 显著主体。**不支持开放词汇**(框不了任意名词) |
| 跟踪 | `VNTrackObjectRequest`(单个种子目标) | 注意:`VNDetectTrajectoriesRequest` **只拟合抛物线/弹道**,不是通用运动跟踪 |

## Apple 没有原生对应的:让 COMPOSERV「聪明」的两件事

- **喂给导演的、有时序的画面 caption。** Vision 只给标签(`VNClassifyImageRequest`)和分数,不给句子。WWDC26 / iOS 27 / AFM 3 给 Foundation Models 加了图像输入(端上 ~3B LLM 能给单图 caption),但:要 iOS 27(约 2026 秋才正式)、**单图无跨帧时序**(做不到 Qwen2.5-VL 多帧一起那种时序理解)、而且是泛化 caption(非动作专项)。Apple 自己开源的 **FastVLM / MobileCLIP** 是可自托管的 VLM 候选(跟自己用 MLX 跑 Qwen 一类),不是系统框架。
- **Claude 级的导演推理。** Foundation Models(WWDC25,macOS 26):端上 ~3B LLM,有 `@Generable` 结构化输出 + 工具调用,但 Apple 明说它「不是通用世界知识的推理器」,必须靠喂进去的上下文。比 Claude 弱很多。WWDC26 加了 20B「Advanced」端上变体,还开放了第三方模型(Claude、Gemini),所以干净路径是:用 Foundation Models 当结构化输出的壳、Claude 当真正的推理。

## 其它

- **人脸身份**(insightface):Apple 没有公开对应(Vision 做人脸检测 / 关键点 / 质量,不做「这是我库里的谁」)。
- **语音转写**:Speech 框架 / WWDC25 `SpeechAnalyzer` + `SpeechTranscriber`(iOS/macOS 26)大概是 Whisper 的对位,但这次没验证。

## 结论(对 COMPOSERV)

高价值的动作是:把 OCR / 美学 / saliency / 抠像 / 检测从昂贵的逐帧 VLM 调用挪到即时的端上 Vision;VLM 只留做时序 caption;Claude 继续当导演;insightface 继续管身份。

## 来源

Apple 一手:developer.apple.com 上面几个 Vision 请求的文档 + Foundation Models 文档;machinelearning.apple.com 的 `apple-foundation-models-2025-updates` 和 `fast-vision-language-models`。
