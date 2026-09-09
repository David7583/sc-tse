# 编程语言脚本编写格式强制模板

版本：v0001  
状态：active  
直接母版：`scripts/action/anchor/sql_writer_v0004.py`

---

## 1. 规范定位

本文件与根目录 `AGENTS.md` 共同构成项目活动规则。

凡在本项目中新建或实质性修改由编程语言编写的源代码，必须先阅读本文件，并以：

```text
scripts/action/anchor/sql_writer_v0004.py
```

作为文件组织、Header、`ALIAS_META`、职责分区、CLI、结构化输出和错误处理的直接母版。

更深目录的 `AGENTS.md` 可以增加局部要求，但不得取消、弱化或静默绕过本文件。

如果本文件与解释性文档存在差异，以本文件和直接母版为准；如果直接母版的业务实现与当前脚本职责不同，只复用格式和可观察契约，不复制 SQL 业务逻辑。

---

## 2. 强制适用范围

本模板只强制约束由编程语言编写的源代码，包括但不限于：

```text
Python / PythonW
JavaScript / TypeScript
PowerShell
Shell / Bash
Batch / CMD
Java / Kotlin
C / C++
C#
Go
Rust
Ruby / Perl
R
MATLAB / Octave
作为可执行程序使用的 SQL
```

同时适用于：

- 活动脚本；
- staging 候选脚本；
- 测试代码；
- CLI、服务入口和启动器；
- 数据迁移、查询、校验和开发工具代码；
- 被其他脚本导入但需要独立维护的程序模块。

---

## 3. 明确不适用范围

本模板不强制约束下列非编程语言文件：

```text
Markdown
纯文本说明
JSON / JSONL / JSON Schema
YAML / TOML / INI
CSV / TSV
普通 HTML / XML 标记文件
CSS 与其他纯样式文件
图片、字体、音视频和二进制文件
静态模板、Prompt、术语表和金标准数据
```

这些文件继续遵循各自的 Schema、配置、文档、数据或项目局部规范。

文件是否适用由其真实职责判断，不因扩展名机械决定。例如：

- 独立执行数据库操作的 SQL 脚本适用；
- 仅保存数据的 `.sql` 片段不自动适用；
- 普通 HTML 页面不适用；
- 在 HTML 中嵌入的程序代码应优先拆入受本模板约束的独立脚本文件。

不得为了满足本模板而给 JSON、YAML、Markdown、HTML 或 CSS 强行添加程序脚本 Header。

---

## 4. 现有代码与任务边界

- 新建编程语言源文件必须完整采用本模板。
- 对既有源文件进行实质性版本升级时，必须在当前任务允许范围内对齐本模板。
- 仅做极小修复时，不得借格式治理重排任务范围外的整个文件；若当前文件格式严重缺失，应报告并由用户决定是否单独治理。
- 不得为了统一格式而批量改写未纳入任务的历史脚本。
- 格式调整不得改变公开接口、字段语义、业务行为、证据链或数据写入边界。

---

## 5. 文件整体顺序

编程语言源文件必须按以下顺序组织：

```text
1. 可选解释器或编码声明
2. 文件说明 Header
3. ALIAS_META
4. future/import/include/use/require 等语言级依赖
5. 全局常量与稳定标识
6. 异常或错误类型
7. 输入、配置和结果数据结构
8. 工具函数区
9. 默认映射与安全默认值
10. 核心类或核心业务组件
11. Schema、契约和兼容性辅助函数
12. CLI / main 接口区
13. 显式程序入口
```

不适用的业务分区可以省略具体实现，但不得打乱其余区块顺序。不得把 CLI 解析、核心业务、正式写入和入口调用无边界混写。

---

## 6. 文件说明 Header 强制模板

使用目标编程语言合法的单行注释包装以下内容，并保持字段顺序：

```text
============================================================
文件名: <实际文件名，包含版本号和扩展名>
中文名: <简洁、明确的中文职责名>
版本号: <vNNNN>

主层级: <data | understand | action>
层级: <模块 / 子模块 / 职责路径>
脚本定位: <一句话说明脚本在调用链中的位置>

职责说明:
- <稳定职责 1>
- <稳定职责 2>

本脚本做什么:
- <当前真实实现 1>
- <当前真实实现 2>

本脚本不做什么:
- <明确排除职责 1>
- <明确排除职责 2>

制度边界声明:
- <数据、安全、事务、人工审核或调用边界 1>
- <失败、回退、覆盖或幂等边界 2>

可更新: <True | False>
============================================================
```

要求：

- `文件名`必须与磁盘真实文件名完全一致；
- Header、文件名、`ALIAS_META.version` 和版本常量必须一致；
- 不得把未来计划写成现有能力；
- 有写操作时必须声明写入目标、覆盖策略和失败行为；
- 涉及 AI 时必须说明模型、Provider、Prompt 和密钥来源边界，但不得记录密钥值；
- 测试脚本也必须说明测试对象、不会污染什么数据以及测试产物位置。

---

## 7. ALIAS_META 强制模板

`ALIAS_META` 必须紧跟 Header，并保持以下顺序：

```text
============================================================
ALIAS_META
============================================================
alias: <真实登记别名>
family: <不含版本号的脚本家族名>
role: <稳定角色名>
version: <vNNNN>
status: <active | deprecated | archived | experimental>
entry_point: <相对项目根目录的真实路径>
input:
  - <输入 1>
output:
  - <输出 1>
depends_on:
  - <依赖 1>
used_by:
  - <真实调用者 1>
============================================================
```

空集合必须显式写为：

```text
input: []
output: []
depends_on: []
used_by: []
```

要求：

- 一个源文件只能有一个有效 `ALIAS_META`；
- `entry_point`不得使用盘符、用户名或绝对路径；
- `input`、`output`、`depends_on`、`used_by` 必须与真实代码一致；
- 未存在的未来调用方必须明确标为 `future`，不得伪装成当前调用关系；
- alias 是否带版本号以当前家族真实制度为准；新家族默认采用直接母版的版本化 alias；
- 进入 `scripts/action` 的文件必须通过 `scan_alias_meta` 检查和适用的 Action 登记流程。

---

## 8. 常量、错误和数据结构

### 8.1 稳定常量

至少集中声明：

```text
DEFAULT_ENCODING
SCRIPT_FAMILY
SCRIPT_NAME
SCRIPT_VERSION
```

路径从 `Path(__file__)`、项目根或配置解析，不得写死盘符和用户名。密钥、Token 和密码不得作为源码常量。

### 8.2 错误类型

复杂脚本必须定义脚本级基础错误，并按实际职责区分：

```text
配置错误
输入或数据错误
Schema / 契约错误
写入或完整性错误
外部依赖或运行错误
```

小型脚本如果不建立异常类，也必须输出等价、稳定的 `error_type`。不得捕获错误后返回伪成功。

### 8.3 数据结构

核心输入、配置和结果必须使用显式结构，例如：

```text
dataclass / Pydantic model / TypedDict
class / struct / record / interface / type
明确版本的 JSON Schema
```

公开字段必须具有稳定语义，不得静默增加、删除或改义。

---

## 9. 工具、默认映射与核心组件

- 工具函数放在核心业务之前，并尽量无副作用；
- 时间使用带时区的 UTC；
- 哈希算法、编码、参与字段和拼接顺序固定；
- 可调参数优先来自版本化配置；
- 配置缺失、损坏或版本不匹配时准确失败；
- 核心组件必须先校验输入和契约，再产生副作用；
- 原始数据默认只读、只追加，不得被派生结果覆盖；
- 一次公开调用必须具有明确的最小事务或原子写入边界；
- 失败不得留下伪完整正式产物；
- 文件句柄、连接、锁和临时资源必须可靠释放。

---

## 10. CLI、dry-run、输出和退出码

具有 CLI 的脚本必须把参数定义放在文件尾部、`main()` 之前。

适用时必须提供：

```text
--config
--dry-run
```

`dry-run` 必须完成输入、配置和契约校验，但不得产生正式持久化副作用。

stdout 只输出机器可读最终结果，推荐单行 JSON：

```json
{"status":"completed","object":"...","object_id":"...","detail":null}
```

错误同样结构化：

```json
{"status":"error","error_type":"DataError","detail":"..."}
```

默认退出码：

| 退出码 | 语义 |
|---:|---|
| `0` | 成功，含明确允许的 existing/duplicate/dry-run |
| `2` | 已分类的配置、输入、数据或契约错误 |
| `3` | 未分类的意外错误 |

调试信息和进度不得污染 stdout JSON。脚本被导入时不得自动执行 CLI。

---

## 11. Python 结构母版

```python
# ============================================================
# 文件名: example_v0001.py
# 中文名: 示例脚本
# 版本号: v0001
#
# 主层级: action
# 层级: example / worker
# 脚本定位: 示例任务的单一职责执行入口
#
# 职责说明:
# - 完成一个边界明确的输入、处理和输出闭环
#
# 本脚本做什么:
# - 校验输入并生成结构化结果
#
# 本脚本不做什么:
# - 不修改原始输入，不执行范围外操作
#
# 制度边界声明:
# - 正式写入采用原子方式，失败不返回伪成功
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: example_v0001
# family: example
# role: example_worker
# version: v0001
# status: active
# entry_point: scripts/action/example/example_v0001.py
# input:
#   - validated input
# output:
#   - structured result
# depends_on:
#   - Python stdlib
# used_by: []
# ============================================================

from __future__ import annotations

import argparse
import json


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "example"
SCRIPT_NAME = "example_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# 异常类型
# ============================================================

class ExampleError(RuntimeError):
    pass


# ============================================================
# 数据结构
# ============================================================

# 使用与职责相符的显式结构。


# ============================================================
# 工具函数区
# ============================================================

# 放置无副作用工具函数。


# ============================================================
# 默认映射
# ============================================================

# 放置安全默认值和版本化配置映射。


# ============================================================
# 核心类
# ============================================================

# 放置核心业务组件。


# ============================================================
# Schema / 契约辅助函数
# ============================================================

# 放置只读契约检查，不顺手迁移 Schema。


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = {"status": "dry_run" if args.dry_run else "completed"}
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except ExampleError as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False, separators=(",", ":")))
        return 2
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False, separators=(",", ":")))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
```

其他编程语言必须保留相同结构职责，并使用本语言合法注释、异常、数据结构、CLI 和入口形式。

---

## 12. 最低验收清单

### 格式

- [ ] 文件名采用 `<family>_vNNNN.<ext>`，特殊入口有明确理由。
- [ ] Header 字段、顺序、空行和分隔线对齐直接母版。
- [ ] `ALIAS_META` 完整、唯一且可扫描。
- [ ] Header、文件名、alias/family/version 与常量相互一致。
- [ ] 导入、常量、错误、数据结构、工具、默认映射、核心组件、契约、CLI 和入口顺序正确。

### 行为

- [ ] 输入在副作用前完成校验。
- [ ] 路径、模型、Provider 和密钥没有写死。
- [ ] 原始数据未被覆盖。
- [ ] `dry-run` 没有正式持久化副作用。
- [ ] stdout 为机器可读结果。
- [ ] 已分类错误返回非零退出码。
- [ ] 导入脚本不会自动执行主流程。

### 验证与登记

- [ ] 语法和模块导入检查通过。
- [ ] 正常、错误、边界、重复执行和回退路径经过测试。
- [ ] 修改未造成任务范围外的排版漂移。
- [ ] 进入 `scripts/action` 的文件完成 staging、扫描和适用登记流程。
- [ ] 完成报告记录测试证据和回退方式。

---

## 13. 关联文件

- 直接母版：`scripts/action/anchor/sql_writer_v0004.py`
- 详细解释：`docs/cross_language_script_format_standard.md`
- Action 开发登记：`docs/development/action_db_feature_development_and_registration_workflow.md`
- 根规则：`AGENTS.md`

本文件是编程语言源代码格式的根目录强制入口；关联文件用于提供实例、解释和具体开发流程，不扩大本文件对非编程语言文件的适用范围。

