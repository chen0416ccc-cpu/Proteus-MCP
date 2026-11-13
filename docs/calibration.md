# 本机初始化

常规使用不再要求手工创建含六种器件的校准工程。首次 `native_create` 或 `native_export` 会从本机安装资源自动生成写入器基线，保存在工作区的 `_local/native-initialization/`。也可以提前执行：

```powershell
python -m proteus_mcp --workspace D:\ProteusWork setup
```

MCP 对应工具为 `native_initialize`，默认参数 `{}`。`proteus_doctor` 只检查条件，不生成资源，也不启动 Proteus。

## 自动准备什么

初始化读取选中安装的 `Samples` 和器件库。它寻找结构已识别的 Proteus 9 容器，移除样例的器件、连线、固件、脚本、调试配置和客户标识，仅保留本机符号/样式表及文件格式设置。器件记录和 DSN/CDB 由写入器重新构造，器件定义来自当前 `.LIB`，不是把样例电路复制成新设计。

当前入口要求 `FILEVER=900..907`、单根原理图和空默认页，器件库索引版本为 `610`。初始化还检查六类内部基线的实际引脚几何；这些基线不是最终电路的器件限制。通用器件支持范围见[限制](limitations.md#器件与原理图)。

缓存记录来源工程、库定义、写入器代码和生成文件的 SHA-256。库、来源或代码改变，缓存损坏或选择了另一套库时，旧缓存失效；重新初始化写入新目录，不覆盖旧包或安装样例。更换工作区后单独初始化，不在 Python 包安装目录写入资源。

如果安装时没有包含可识别的样例，返回 `BOOTSTRAP_SOURCE_NOT_FOUND`。先在自己的合法安装中补装 Samples，或使用下文的高级覆盖；不会自动从网上下载商业工程。

## 原生保存重开

默认 `setup` 仅准备并检查文件。需要进一步验证当前安装时执行：

```powershell
python -m proteus_mcp --workspace D:\ProteusWork setup --verify-native
```

MCP 调用为 `native_initialize {"verify_native": true}`。需要 Windows、`[bridge]` 依赖及匹配的原生适配配置。它会在独立会话中打开生成的基线，保存、正常关闭、重新打开，并核对真实器件位置、引脚坐标及导线折点。全程不截屏、不模拟鼠标键盘；不运行基线电路的仿真。

先保存并退出外来 Proteus 进程，否则返回 `busy`。失败时保留会话、错误和 `native-verification.json`，不接管已有文档、不强杀、不自动确认未知弹窗。

`ready` 表示写入器资源可用，只有实际完成上述流程才返回 `native_reopen_verified: true`。基线验收不等于每个新导出的电路已通过仿真，具体输出仍分别检查。

## 状态与诊断

`proteus_doctor.native_initialization` 区分以下状态：

| 状态 | 含义 |
| --- | --- |
| `initialization_required` | 已找到可用资源，首次导出可自动准备 |
| `ready` | 缓存或显式基线已通过结构与完整性检查 |
| `unavailable` | 缺少来源、器件库或文件格式不支持，详见 `reason` |
| `invalid_override` | 显式指定的基线无效，不静默退回自动发现 |

文件初始化和运行时 ABI 适配是两回事。自动生成基线不会让未知 Proteus 构建获得内部函数兼容性；构建诊断位于 `proteus_doctor.native_build`，见[兼容范围](limitations.md#兼容范围)。

## 高级覆盖

保留已有私有基线时，可传 `--calibration`，或设置：

```powershell
$env:PROTEUS_MCP_CALIBRATION = 'D:\ProteusLocal\template.pdsprj'
python -m proteus_mcp doctor
```

覆盖文件必须已经包含支持的六类基线、POWER/GROUND 和真实正交导线，并通过 DSN/CDB、默认方向和页目录检查。它只读使用，不自动覆盖或执行 `setup --verify-native`；需要验证时先复制到工作区，再调用工程生命周期工具。

命令行参数优先于环境变量，路径在每个服务实例创建时解析，不同服务互不串用。源码中的 `scripts/check_calibration.py` 可作独立只读检查。

## 资源范围

生成文件含本机厂商资源，只留在本机。不要把缓存、完整器件库、私有模板、授权文件或课程工程提交到仓库或作为开源附件分发。发布审计仍拒绝原生工程和商业二进制文件。
