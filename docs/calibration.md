# 本机校准模板

新原生文件写入器需要一个在目标 Proteus 安装中保存的基线 `.pdsprj`。其中的容器、数据库、符号和端子记录作为本机模板使用。模板可能包含厂商资源，因此本项目不随源码、wheel、sdist 或 Release 分发它。

这不是“下载一个空工程就能支持所有版本”的机制。模板匹配只解决文件写入的基线，运行时桥的模块校验、器件引脚图元支持和实际保存重开是独立条件。

## 一次性准备

在自己有权使用的 Proteus 安装中创建一个独立测试工程，存放在仓库之外，例如 `D:\ProteusLocal\10-terminal-wire.pdsprj`。

1. 放入以下六种器件，每种一个：`AT89C51`、`RES`、`LED-RED`、`CAP-ELEC`、`CAP`、`CRYSTAL`。建议分别标为 U1、R1、D1、C1、C2、X1。
2. 六种器件均使用默认方向，不旋转、不镜像。暂不加入子电路、图表、复杂多单元或未知符号。
3. 放入 POWER 和 GROUND 端子，即原生 `$TERPOWER`、`$TERGROUND` 类型。
4. 至少保存一条真正的正交导线，连接真实器件引脚。已校准基线使用 U1.1 到 R1.2，另有 D1 到 POWER 的导线。不用图形线代替电气导线，不要斜线。
5. 保存、关闭并重新打开，确认工程本身有效。模板中的演示连接不是工作电路，不要将其作为最终电路仿真。

本项目不自动替你完成首次原生模板准备，也不承诺任意包含这六种器件的手工工程都已完成格式校准。

## 只读结构检查

在源码仓库中执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\check_calibration.py D:\ProteusLocal\10-terminal-wire.pdsprj
```

脚本检查器件种类、默认方向、端子、导线、DSN/CDB 一致性和正交性，并初始化写入器以排除缺少基线记录的情况。它只读文件，不复制、不安装、不修改模板。

`status: passed` 仅表示结构门槛通过，输出仍标记 `native_reopen_verified: false`、`simulation_verified: false`。如出现解析异常或未知记录，保留模板副本和错误信息，停止原生导出，不要手工删除二进制记录“修复”。

## 指定模板

```powershell
$env:PROTEUS_MCP_CALIBRATION = 'D:\ProteusLocal\10-terminal-wire.pdsprj'
.\.venv\Scripts\python.exe -m proteus_mcp doctor
```

MCP 客户端配置中的 `env` 也要设置同一变量。模板路径在模块导入时读取，修改后需要重启 MCP 服务。

`doctor` 检查模板文件是否存在，不会自动替你执行上述结构检查。首次使用还需要在独立工作区生成一个小例子，完成 `native_read`、`project_open`、`project_save`、`project_reopen` 和仿真检查。见[流水灯示例](examples.md#八路流水灯)。

## 保护模板和资源

- 不要把真实课程工程、个人资料、授权文件或全部器件库当作模板上传。
- `.gitignore` 和发布审计会拒绝原生工程，但这不替代提交前的人工检查。
- 同名器件来自不同库时，明确选择 `library_path`；库文件改变后重新读取器件定义，不沿用旧哈希。
- 更换 Proteus 构建后重新评估模板和运行时桥，不能只改环境变量继续使用旧适配器。
