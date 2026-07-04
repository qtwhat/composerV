# CONFIRM 阶段设计（感知后的人机确认）

日期：2026-07-01
状态：设计已确认，待写实现计划
关联：`composerv_pipeline_overview.html` 里标为「规划中」的 ③ 确认 CONFIRM 阶段

## 背景与目的

现在流水线是 catalog → analyze → montage(director) → render，感知结果直接进导演，中间没有人类介入的机会。本阶段在 analyze 之后、montage 之前插入一次人机确认，做两件事：

1. 人像确认：把感知检测到的人物簇拿给用户，对照本地人物库确认「这是谁」，用户可命名 / 标记敏感 / 写一句人物备注，也可跳过。名字与备注写回本地数据库，长期保存并用于以后自动识别。
2. 用户输入：让用户为「这次要剪的这批素材」填一份总 brief（整体上下文 + 风格/节奏），作为最高优先级的人类指引交给导演，呼应导演已有的 rule 1（Human-led）。

面部识别的底子已存在（`faces/` 模块 + `persons`/`faces` 表 + `name`/`merge` CLI + 只读 contact sheet）。本阶段补的是：把它整合成 analyze 后的一次确认、加上自由文本 brief 的采集，并把这些注入导演。

## 决策记录

| 决定 | 理由 | 否决的替代 |
|---|---|---|
| 交互用「浏览器表单 + 本地小服务」 | 人脸必须看图才能认；看图、命名、填输入在一处最顺；用户偏好可视化界面 | 纯终端 Q&A（看图与命名分两处）；纯 CLI 增量（不成连贯环节） |
| brief 粒度 = 整批素材一份总 brief | 贴合用户描述「整段视频的上下文/风格」；注入导演最简单 | 每条片段 note（量大）；两者都要（YAGNI） |
| brief 内容 = 两个自由文本框（上下文、风格/节奏），建议性质不硬覆盖 | 最灵活；不与导演已有的 feeling 推断 / 时长参数冲突 | 加结构化字段（feeling 覆盖 / 目标时长）与现有参数重叠 |
| 人像确认 = 名字 + 敏感 + 一句人物备注 | 备注让导演的 who 更有上下文（如「小明（我女儿）」）；成本低 | 只要名字（上下文全塞进 brief） |
| brief 按 scope 字符串绑定 | `confirm <scope>` 与 `montage <scope>` 用同一 scope 参数自然配对 | 按拍摄日（跨天要分填）；单例当前 brief（多项目并行会串） |
| confirm 自己保证人脸就绪（检测 + 聚类） | 目前检测未接进正式流程，否则 confirm 可能没脸可显示；复用 `faces/enroll.py` | 只聚类、假设别处已检测（当前不成立） |

证伪条件（哪种情况说明设计需要改）：如果用户实际更想逐条片段写备注、或需要 brief 硬覆盖 feeling/时长、或本地服务在用户环境跑不起来（端口/浏览器限制），则相应决策需重议。

## 架构总览

新命令 `composerv confirm <scope>`，跑在 analyze 之后、montage 之前：

1. 解析 scope 成一批 clip 路径（复用 `cli/main.py` 现有 scope 解析逻辑，与 analyze/montage 一致）。
2. 确保人脸就绪：对这批里没有人脸记录的 clip 跑检测，再全局 `cluster_all()`（复用 `faces/enroll.py`）。`--no-detect` 可跳过检测只做聚类。
3. 起本地 HTTP 小服务（标准库 `http.server`），自动打开浏览器到表单页。
4. 用户在网页命名/标敏感/写人物备注（可跳过），填两个 brief 文本框（预填该 scope 已存的 brief）。
5. 提交 → POST 写回数据库（`persons` 名字/敏感/备注 + `briefs`）→ 提示已保存 → 服务退出。

## 部件详情

### 1. store（`composerv/store/db.py`）

数据模型：
```python
class Person(BaseModel):        # 已有，新增 note
    person_id: int
    name: str = ""
    sensitive: bool = False
    centroid: list[float] = []
    n_faces: int = 0
    note: str = ""              # 新增：一句人物备注（角色/关系）

class Brief(BaseModel):         # 新增
    scope: str
    context: str = ""           # 整体上下文（什么场合 / 突出谁 / 避开什么）
    style: str = ""             # 风格与节奏
    updated_at: str = ""
```

表结构：
- `persons` 加一列 `note TEXT DEFAULT ''`。对已存在的库做一次轻量迁移（`ALTER TABLE persons ADD COLUMN note` 时先检查列是否存在），与 store 现有建表方式一致。
- 新表 `briefs(scope TEXT PRIMARY KEY, context TEXT DEFAULT '', style TEXT DEFAULT '', updated_at TEXT DEFAULT '')`。

新增方法：
```python
def set_person_note(self, person_id: int, note: str) -> None
def set_brief(self, scope: str, context: str, style: str) -> None
def get_brief(self, scope: str) -> Brief | None
def clip_person_labels(self, asset_path: str, include_sensitive: bool = True) -> list[str]
    # 带备注的名字，如 "小明（我女儿）"；无备注时就是 "小明"。给导演 who 用。
    # clip_person_names 保留不动（其它地方仍可能用）。
```

### 2. confirm 模块（新增 `composerv/confirm/`）

纯函数（可单测，不碰 IO）：
```python
# form.py
class PersonUpdate(BaseModel):
    person_id: int
    name: str = ""
    sensitive: bool = False
    note: str = ""
class BriefInput(BaseModel):
    context: str = ""
    style: str = ""

def render_confirm_form(rows: list[PersonRow], brief: Brief | None, *, crop_url) -> str
    # 生成表单 HTML。人物区：每人代表脸图（crop_url(person_id) 指向本地服务的图片端点）
    #   + 名字输入 + 敏感勾选 + 备注输入，均预填现值。
    # brief 区：两个 textarea（上下文、风格），预填 brief。
    # 复用 faces/review.py 的 person_rows / PersonRow。
def parse_confirm_submission(form: dict) -> tuple[list[PersonUpdate], BriefInput]
    # 把提交的表单字段解析成结构化更新。纯函数。
```

IO 薄层：
```python
# server.py
def serve_confirm(store, scope: str, *, port: int = 0, open_browser: bool = True) -> None
    # 起 http.server：
    #   GET /        -> person_rows(store) + get_brief(scope) -> render_confirm_form
    #   GET /crop?id -> 返回该人物代表脸的 crop 图片（读 crop_path 文件）
    #   POST /save   -> parse_confirm_submission -> set_person_name/set_person_note/set_brief -> 200
    # 保存后置一个「已保存」页面，主线程收到保存事件后关闭服务。

# enroll_glue.py（或直接在命令里）
def ensure_faces(store, paths: list[str], *, detect: bool = True) -> tuple[int, int]
    # 对 paths 里没有人脸记录的 clip 调 detect_clip_faces；再 cluster_all(store)。复用 faces/enroll.py。
```

### 3. CLI（`composerv/cli/main.py`）
```python
@app.command()
def confirm(scope: str = typer.Argument("selected"),
            db: str = typer.Option(default_db(), ...),
            port: int = typer.Option(0, ...),
            no_detect: bool = typer.Option(False, "--no-detect", ...)) -> None
    # 解析 scope -> paths；ensure_faces(store, paths, detect=not no_detect)；serve_confirm(store, scope)。
```

### 4. director（`composerv/director/`）
- `montage` CLI 命令按 `scope` 取 `store.get_brief(scope)`，把它传下去。
- `build_director_montage(..., brief: Brief | None = None)`：把 brief 透传给 prompt 构建。
- `build_director_prompt(..., brief_context: str = "", brief_style: str = "")`：在 PREAMBLE 之后、footage table 之前加一段最高优先级指引，例如：
  ```
  用户输入（HUMAN BRIEF, highest priority — follow it over your own judgement):
  - Context: {brief_context}
  - Style / pacing: {brief_style}
  ```
  仅在 brief 非空时插入。rule 1（Human-led）已支持这一优先级，措辞上呼应它。
- who 字段改用 `store.clip_person_labels(p)`（`director/montage.py` 里 `"people": ...` 那行），带出人物备注。

## 数据流

```
analyze（或 confirm 内的检测）
  → confirm <scope>：用户命名 + 人物备注 + 两个 brief 文本框
      → store：persons.name/sensitive/note、briefs(scope)
  → montage <scope>：读 get_brief(scope) + clip_person_labels
      → build_director_prompt 注入「用户输入」段 + 更丰富的 who
  → 导演剪辑
```

## 测试计划（沿用仓库 TDD + 依赖注入风格）

纯函数单测：
- store：`set_brief`/`get_brief` 往返；`set_person_note` + `clip_person_labels` 渲染「名字（备注）」与无备注两种；新库与旧库（迁移后）都能读写。
- `render_confirm_form`：含名字/敏感/备注输入与预填值；brief 两框预填；无人物时只出 brief 区。
- `parse_confirm_submission`：正常提交、空提交、部分字段缺失都解析正确。
- `build_director_prompt`：给了 brief 时含「用户输入」段且内容正确；brief 为空时不插入该段。

IO 层：
- `serve_confirm` 一个冒烟测试：构造请求，GET 出表单含预期字段、POST 后 store 里出现对应人名/备注/brief。用测试用 store，不起真实浏览器。
- `ensure_faces` 用注入的假 detector 测「只对缺人脸的 clip 检测 + 之后聚类」。

浏览器本身不进单测。

## 边界与错误处理
- 无人脸检测结果：表单只显示 brief 区，提示「未检测到人脸」。
- 空提交：不改任何东西，等于跳过。
- 端口占用：`port=0` 让系统选空闲端口；显式端口占用则报错提示换端口。
- insightface 未安装：检测步骤打印警告并跳过，仍可填 brief（与 aesthetics「缺二进制则静默降级」的约定一致）。

## 非目标（本期不做）
- 网页内合并同一个人（split cluster 合并）：沿用现有 `composerv merge` CLI。
- 每条片段单独备注（复用 `note` 字段的逐片流程）。
- brief 硬覆盖 feeling / 目标时长等结构化控制。

## 后续
- 实现并测通后，把 `composerv_pipeline_overview.html` 里 ③ 确认 CONFIRM 的状态从「规划中」改为「已实现」，并把 UserBrief 提案契约更新为实际的 `Brief` / `Person.note`。
