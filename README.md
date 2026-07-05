# CPA ⇄ sub2api Bridge 使用说明 / Quick Guide

> 大白话版：这个工具就是帮你把 **CPA 格式** 和 **sub2api 格式** 互相转换。  
> Plain English: this tool converts between **CPA format** and **sub2api format**.

---

## 中文说明

### 这个工具是干嘛的？

它可以自动判断你丢进去的文件是什么格式，然后自动转换：

| 你放进去的东西 | 自动输出 |
|---|---|
| CPA 压缩包 / CPA 文件夹 / 单个账号 JSON | sub2api JSON |
| sub2api JSON 文件 | CPA ZIP |
| sub2api 链接 | CPA ZIP |

简单说：

- CPA → sub2api
- sub2api → CPA
- 自动识别，不用你手动选格式

---

### 支持哪些文件？

支持常见格式：

- `.zip`
- `.tar`
- `.tar.gz`
- `.tgz`
- `.gz`
- `.bz2`
- `.xz`
- `.json`
- 文件夹

如果你的电脑安装了 **7-Zip** 或 **WinRAR**，还可以支持：

- `.7z`
- `.rar`
- `.zipx`
- `.cab`

---

### Windows 怎么用？

推荐用这个：

```text
拖拽到我身上自动转换.cmd
```

操作方法：

1. 解压工具包
2. 把你的 CPA 压缩包、sub2api JSON 或文件夹拖到 `拖拽到我身上自动转换.cmd` 上
3. 等它自动转换
4. 输出文件会生成在原文件旁边

备用方法：

```text
RUN_MANUAL_BRIDGE.cmd
```

双击它，然后手动粘贴文件路径或链接。

---

### 如果失败怎么办？

先双击：

```text
TEST_PYTHON.cmd
```

确认 Python 能不能正常运行。

常见问题：

| 报错 | 原因 | 解决方法 |
|---|---|---|
| Error code: 9009 | Python 命令没找到或是假命令 | 安装 Python，或用新版启动器 |
| File is not a zip file | 文件后缀是 zip，但内容不是 zip | 重新下载，或确认真实格式 |
| No accounts found | 文件里没找到账号数据 | 检查输入文件是不是正确 |
| 乱码 | 旧版 bat 编码问题 | 使用新版纯英文 `.cmd` |

---

### 安全提醒

这些文件里可能包含登录凭证、token、邮箱等敏感信息。

请不要：

- 把转换后的 JSON / ZIP 上传到 GitHub
- 把真实账号数据发给别人
- 在公开群、论坛、网盘分享真实输出文件

建议：

- 只处理你自己拥有或被授权管理的账号
- 给仓库添加 `.gitignore`
- 只上传代码，不上传真实账号数据

---

## English Guide

### What does this tool do?

It automatically detects the input format and converts it:

| Input | Output |
|---|---|
| CPA archive / CPA folder / single account JSON | sub2api JSON |
| sub2api JSON file | CPA ZIP |
| sub2api URL | CPA ZIP |

In simple words:

- CPA → sub2api
- sub2api → CPA
- Auto-detects the direction

---

### Supported inputs

Built-in support:

- `.zip`
- `.tar`
- `.tar.gz`
- `.tgz`
- `.gz`
- `.bz2`
- `.xz`
- `.json`
- folders

With **7-Zip** or **WinRAR** installed:

- `.7z`
- `.rar`
- `.zipx`
- `.cab`

---

### How to use on Windows

Recommended:

```text
拖拽到我身上自动转换.cmd
```

Steps:

1. Extract the tool package
2. Drag your CPA archive, sub2api JSON, or folder onto `拖拽到我身上自动转换.cmd`
3. Wait for conversion
4. The output file will be created next to the input file

Alternative:

```text
RUN_MANUAL_BRIDGE.cmd
```

Double-click it and paste a file path or URL manually.

---

### Troubleshooting

Run this first:

```text
TEST_PYTHON.cmd
```

Common issues:

| Error | Reason | Fix |
|---|---|---|
| Error code: 9009 | Python command not found or broken alias | Install Python or use the fixed launcher |
| File is not a zip file | The extension is zip, but the content is not zip | Re-download or check the real format |
| No accounts found | No account data detected | Check your input file |
| Garbled text | Old batch encoding issue | Use the latest ASCII-safe `.cmd` files |

---

### Security Notice

The converted files may contain sensitive login credentials, tokens, and email addresses.

Do not:

- Upload real output JSON / ZIP files to GitHub
- Share real account data with others
- Publish generated credential files online

Recommended:

- Only process accounts you own or are authorized to manage
- Add a `.gitignore`
- Upload source code only, never real account exports
