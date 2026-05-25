---
name: novel-reader
description: 使用 novel-reader 工具阅读和分析长篇小说的统一技能。
---

# Novel Reader 统一技能

使用此技能阅读完整的长篇小说，特别是超出单次上下文窗口的 TXT/Markdown 作品。

## 核心规则

- 默认使用中文输出，除非用户明确要求其他语言
- 除非 `novel-reader status` 显示 100% 摘要覆盖率，否则不要声称已完全阅读整本书
- 对于事实性剧情问题，先使用 `novel-reader do <book_id> "<问题>"` 获取证据，再回答
- 语言风格提炼应提供原创写作迁移指南，而不是直接模仿特定作者的提示词
- 续写前必须先使用 `novel-reader write-next` 构建任务包，不能仅凭记忆续写

## 快速开始

### 首次设置

从插件根目录使用内置包装器：

```bash
python ./bin/novel-reader ingest path/to/book.txt
```

该命令会打印一个 `book_id`，后续所有命令都使用该 ID。

### 日常使用（统一入口）

日常操作使用 `do` 命令，通过自然语言描述需求：

```bash
python ./bin/novel-reader do <book_id> "你的自然语言需求"
```

**常见场景示例**：

```bash
# 查看阅读进度
python ./bin/novel-reader do <book_id> "这本书现在读到哪了"

# 阅读特定章节
python ./bin/novel-reader do <book_id> "读第3章"

# 搜索内容
python ./bin/novel-reader do <book_id> "搜索主角"

# 提问剧情问题
python ./bin/novel-reader do <book_id> "主角为什么背叛组织"

# 梳理剧情
python ./bin/novel-reader do <book_id> "梳理剧情大纲"

# 分析写作风格（特定场景）
python ./bin/novel-reader do <book_id> "帮我分析战斗场景怎么写"

# 续写
python ./bin/novel-reader do <book_id> "接第12章后面续写，短一点，偏悬疑"
```

### 高级：续写任务

对于正式的续写任务，使用专门的 `write-next` 命令：

```bash
python ./bin/novel-reader write-next <book_id> --after-chapter 12 --outline "主角潜入北塔"
```

## 当 do 命令失败时

如果 `do` 命令无法正确识别意图，可以使用底层命令。底层命令包括：

```bash
novel-reader status <book_id>
novel-reader read <book_id> --chapter 3
novel-reader search <book_id> "关键词"
novel-reader ask <book_id> "问题"
novel-reader outline <book_id>
novel-reader map <book_id>
novel-reader analyze <book_id>
novel-reader style <book_id>
novel-reader continue <book_id> --after-chapter 12
novel-reader embed <book_id>
```

## 注意事项

- `do` 命令会自动识别意图，但对于复杂需求，建议结合显式参数使用
- 使用 `--json` 标志获取结构化输出
- 对于语义搜索，先使用 `novel-reader embed` 建立向量索引，再使用 `--semantic`
