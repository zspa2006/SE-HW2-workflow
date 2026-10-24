# 代码文档生成 Workflow

作者：潘彦博（学号：2404080334）  
软件项目管理 Homework 2

这次作业选择的是“文档生成流程”。我在上次代码解释 Agent 的基础上，把工作分成代码分析、文档生成和审校三个部分。输入 Python 文件后，程序会生成中文说明文档；如果审校发现遗漏，就把问题返回给文档 Agent，修改后再检查。

项目名称为 DocFlow，使用 Python 标准库实现，支持命令行运行。

## 1. 功能

- 三个 Agent 按顺序协作，共享代码分析结果、文档草稿和审校意见。
- 文档包含函数签名、说明和源码行号，方便对照代码查看。
- 审校发现缺少章节或接口时，返回文档 Agent 修改，默认最多返修两次。
- 每一步保存日志和状态，暂停或失败后可以从检查点继续。
- 流程通过配置定义，可以修改路由，也可以注册新的 Agent。
- 提供本地 HTML 报告，可以直接查看执行过程和生成的文档。

## 2. 运行

需要 Python 3.10 或以上版本，不需要安装第三方依赖。本次测试使用 Windows 和 Python 3.12.8。

在项目文件夹打开 PowerShell，执行：

```powershell
python main.py
```

菜单中可以选择运行示例、演示返修、演示恢复，或者分析自己的文件。示例默认使用离线模式，不需要密钥。

也可以直接使用带参数的命令：

```powershell
python -m docflow run --root examples/shop --files pricing.py orders.py
```

`--root` 是待分析项目的根目录，`--files` 是要分析的文件。示例中，`pricing.py` 负责计价，`orders.py` 负责订单汇总。

正常执行顺序为：

```text
分析 Agent → 文档 Agent → 审校 Agent → 完成
```

控制台显示 `succeeded` 后，可以在对应的 `runs/运行编号/` 中找到结果：

| 文件 | 内容 |
| --- | --- |
| `README.generated.md` | 审校通过后的代码文档 |
| `report.html` | 执行报告，可用浏览器直接打开 |
| `review.json` | 最终审校结果 |
| `state.json` | 当前状态、消息记录和各 Agent 的结果 |
| `events.jsonl` | 执行日志和工具调用记录 |
| `step-*.json` | 每次节点完成时保存的结果，包括返修前后的版本 |

也可以先查看已经生成的 [正常运行报告](docs/evidence/normal/report.html) 和 [54 秒演示视频](docs/演示视频.mp4)。视频使用中文字幕，无旁白。

报告中的“每一步的结果”可以展开查看分析结果、各版草稿和审校意见。例如返修成功后，仍能看到第一轮为什么没有通过。

## 3. 演示返修流程

```powershell
python -m docflow run --root examples/shop --files pricing.py orders.py --demo-revision
```

这个参数会让初稿故意漏掉“验证建议”一章，用来检查审校和返修是否有效。第一次审校发现问题后，程序返回文档 Agent 补齐，再次审校通过。执行顺序为：

```text
分析 → 文档 → 审校（不通过）→ 修改文档 → 审校（通过）
```

结果应为 5 次节点执行、1 次返修。第一轮的问题和第二轮的文档都保存在运行目录中。[返修报告](docs/evidence/revision/report.html) 中可以查看对应消息。

不加 `--demo-revision` 时不会主动漏章。`--max-revisions` 可以设置最多返修次数，默认为 2；超过上限会保留草稿和问题清单，并结束本次运行。

## 4. 暂停和恢复

下面的命令会在分析 Agent 完成后暂停：

```powershell
python -m docflow run --root examples/shop --files pricing.py orders.py --stop-after analyze
```

找到控制台显示的运行目录，再执行：

```powershell
python -m docflow inspect runs/运行编号
python -m docflow resume runs/运行编号
```

请把 `运行编号` 换成实际目录名。恢复后只运行文档和审校，已经完成的分析不会重复执行。项目移动位置后，可以用 `resume ... --root examples/shop` 指定新的源码目录。

恢复前会核对源码的 SHA-256。如果文件内容变了，需要重新运行，避免旧的分析结果和新的代码混在一起。

`python scripts/demo.py` 可以一次生成正常、返修和恢复三组演示。

## 5. 模型配置

默认使用 `offline` 模式，通过 AST 提取代码结构，再根据规则和模板生成文档。这种方式不需要密钥，主要用于运行演示和检查流程。缺少 docstring 的接口会提示需要结合源码查看，不会自动推断完整业务逻辑。

如果希望使用模型解释代码，可以选择 `llm` 模式。三个 Agent 会分别请求模型，审校时会同时提供源码和文档。

```powershell
$secureKey = Read-Host '请输入模型 API Key' -AsSecureString
$env:DOCFLOW_API_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
$env:DOCFLOW_BASE_URL = 'https://api.openai.com/v1'
$env:DOCFLOW_MODEL = Read-Host '请输入可用的模型名'
python -m docflow run --root examples/shop --files pricing.py orders.py --mode llm
```

接口采用 Chat Completions 兼容格式，基址和模型名按实际使用的服务填写，程序会在基址后加 `/chat/completions`。`.env.example` 只列出配置项，程序不会自动读取该文件。接口格式参考 [官方文档](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。

模型部分目前使用本地 HTTP 模拟服务测试了请求、响应和三个节点的调用过程，还没有用真实账号调用云端模型。模型模式会把选定的源码和文档发给配置的服务，密钥不写入日志或状态文件。

## 6. 修改流程和增加 Agent

默认流程配置在 `examples/flow.json`：

```powershell
python -m docflow run --root examples/shop --files pricing.py orders.py --flow examples/flow.json
```

增加 Agent 时，实现 `run(context)`，返回统一的 `Result`，再加入注册表和流程配置即可。项目提供了一个单独的扩展示例：

```powershell
python scripts/extend_demo.py
```

它在分析后增加一个核对节点，检查分析结果是否包含所有输入文件，再继续生成和审校文档。这个示例用于说明扩展方式，正常作业流程仍使用三个 Agent。具体代码见 [scripts/extend_demo.py](scripts/extend_demo.py)，[运行结果](docs/evidence/extension/report.html) 和 [设计说明](Design.md) 也保存在项目中。

## 7. 测试

```powershell
python -m unittest discover -s tests -v
```

共 44 项测试，43 项通过，1 项因当前 Windows 环境没有创建符号链接的权限而跳过。主要检查完整流程、Agent 消息、返修、恢复、异常处理和新增节点。详细结果见 [测试报告](docs/测试报告.md)。

## 8. 文件结构

```text
main.py          菜单入口
docflow/          Agent、流程引擎、安全读取、模型接口和命令行
examples/         待分析代码与流程配置
tests/            自动测试
scripts/          演示、扩展和打包脚本
docs/evidence/    正常、返修、恢复三组运行结果
README.md         使用方法
Design.md         流程图、架构和状态设计
```

## 9. 目前的限制

目前只支持明确指定的 Python UTF-8 文件，单个文件最多 32,000 字节，总计最多 30 个文件、160,000 字节。程序只做静态分析，不执行或修改输入代码。局部函数、动态生成的接口和同作用域重名定义暂不支持完整分析。

流程采用单进程顺序执行，支持条件分支和返修循环，没有实现并行调度和拖拽编排。异常退出后，未保存完成的节点可能需要重跑。若留下 `.run.lock`，需确认原进程已经退出，再删除锁文件后恢复。

文档中的代码说明仍需要对照源码核实，审校通过表示满足当前的检查规则，不代表已经验证了所有业务逻辑。
