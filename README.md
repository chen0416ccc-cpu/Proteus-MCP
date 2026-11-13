# Proteus MCP

[![CI](https://github.com/chen0416ccc-cpu/Proteus-MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/chen0416ccc-cpu/Proteus-MCP/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

让支持 MCP 的 AI 客户端查询 Proteus 器件库、编辑电路、运行仿真，并读取真实引脚和网络状态。

绘图不是模拟鼠标点击。离线部分直接读取库和工程文件，运行时部分通过本机原生桥调用 Proteus 功能。原理图、固件、保存后的工程和仿真结果分开检查，不把“代码编译成功”当作“电路已经跑通”。

> **实验版，先确认兼容性。** 原生桥只支持通过 SHA-256 校验的特定 Proteus 9 构建，不是通用的 Proteus 官方 API。新建原生电路需要自行准备本机校准模板，仓库不提供 Proteus、Keil、授权文件或器件库。请先阅读[安装要求](docs/installation.md)和[已知限制](docs/limitations.md)。

[快速开始](#快速开始) · [工具](docs/tools.md) · [示例](docs/examples.md) · [校准模板](docs/calibration.md) · [问题反馈](https://github.com/chen0416ccc-cpu/Proteus-MCP/issues)

## 能做什么

| 场景 | 当前支持 |
| --- | --- |
| 查器件 | 搜索本机 `.LIB`，读取物理引脚、隐藏电源、属性及来源哈希 |
| 画原理图 | 语义电路批量编辑、四向旋转/镜像、正交连线、汇接点和文字布局，导出新的 `.pdsprj` |
| 改当前电路 | 读取 MCP 自有会话，添加、连接、移动、修改属性，精确删除支路/汇接点，撤销与重做 |
| PCB 布线 | 双层 TOP/BOT、通孔换层、有界单线/多线推挤；只处理已校准的几何形状 |
| 检查 | 原生 ERC、物理间距 DRC、23 项预生产检查，以及已保存工程的 CSV/JSON BOM |
| 固件与仿真 | 调用本机 C51 工具链，打开/保存/重开工程，运行/暂停/停止，读取日志和数字网络采样 |
| 交互验证 | BUTTON/LOGICSTATE 激励、仿真时钟、四位数码管采样解码，以及流水灯/外部中断示例 |

共 53 个工具，完整列表见[工具参考](docs/tools.md)。不支持的操作会明确返回错误或未确认状态，不会退回截图猜测后假装成功。

自动绘图按信号网络、引脚方向、器件和标注尺寸生成候选布局，再选择通过检查的布线方案，不依赖八灯等示例的器件编号。默认使用完整信号线，不会在失败后换成标签短线。写入前和文件读回后都检查连通、碰撞与蓝框范围；检查失败就返回原因。详见[布局与布线](docs/drawing.md)。

## 环境要求

- Windows 10/11，Python 3.11 或更新版本。
- 自行安装并合法使用 Proteus。原生控制要求安装文件匹配[兼容配置](docs/limitations.md#兼容范围)，仅版本号相同还不够。
- 原生桥依赖 Frida，安装时选用 `bridge` 扩展。
- 编译 8051 示例需要 Keil C51 的 `C51.exe`、`BL51.exe` 和 `OH51.exe`；仅查询库、编辑语义电路不需要 Keil。
- 导出新原生工程需要[本机校准模板](docs/calibration.md)。没有模板仍可使用库查询、语义模型和已支持格式的文件读取。

Linux/macOS 可以运行部分离线逻辑和可移植测试，但不能运行 Proteus 原生桥。GitHub CI 不替代 Windows 上的原生验收。

## 快速开始

### 1. 安装

以下命令在 PowerShell 中执行。示例目录可以换成自己的路径。

```powershell
git clone https://github.com/chen0416ccc-cpu/Proteus-MCP.git D:\Projects\Proteus-MCP
cd D:\Projects\Proteus-MCP
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[bridge]"
```

也可以从 [Releases](https://github.com/chen0416ccc-cpu/Proteus-MCP/releases) 下载 wheel，在独立虚拟环境中安装。当前未发布到 PyPI，请勿将同名 PyPI 包视为本项目。

### 2. 指定本机路径

```powershell
$env:PROTEUS_MCP_EXE = 'C:\Program Files\Labcenter Electronics\Proteus 9 Professional\Bin\PDS.exe'
$env:PROTEUS_MCP_LIBRARY = 'C:\ProgramData\Labcenter Electronics\Proteus 9 Professional\Library'
$env:PROTEUS_MCP_KEIL = 'C:\Keil_v5'
$env:PROTEUS_MCP_WORKSPACE = 'D:\ProteusWork'
# 完成本机模板准备后，再添加这一项：
# $env:PROTEUS_MCP_CALIBRATION = 'D:\ProteusLocal\10-terminal-wire.pdsprj'

.\.venv\Scripts\python.exe -m proteus_mcp doctor
```

`doctor` 按能力报告结果。`available` 表示依赖或配置满足相应门槛，不代表已经打开工程、验证连线或完成仿真。模块哈希不匹配时请停止原生操作，不要绕过检查。

### 3. 接入 MCP 客户端

以 Codex 的 `config.toml` 为例。客户端进程通常不会继承刚在另一个终端设置的变量，因此在配置中显式填写：

```toml
[mcp_servers.proteus]
command = 'D:\Projects\Proteus-MCP\.venv\Scripts\python.exe'
args = ["-X", "utf8", "-m", "proteus_mcp", "serve"]

[mcp_servers.proteus.env]
PROTEUS_MCP_WORKSPACE = 'D:\ProteusWork'
PROTEUS_MCP_EXE = 'C:\Program Files\Labcenter Electronics\Proteus 9 Professional\Bin\PDS.exe'
PROTEUS_MCP_LIBRARY = 'C:\ProgramData\Labcenter Electronics\Proteus 9 Professional\Library'
PROTEUS_MCP_KEIL = 'C:\Keil_v5'
# PROTEUS_MCP_CALIBRATION = 'D:\ProteusLocal\10-terminal-wire.pdsprj'
```

其他 stdio 客户端可使用相同的 `command`、`args` 和 `env`，JSON 示例见[安装文档](docs/installation.md#其他客户端)。服务不提供 HTTP 监听端口。

### 4. 先做一个小例子

模板和原生桥均就绪后，可以直接对 Agent 说：

```text
先运行 proteus_doctor。兼容性通过后，新建 led_demo，使用 12 MHz 的
AT89C51，在 P1 口接 8 个低有效 LED，编译 C51 流水灯程序。
导出原生工程并回读引脚和网络，确认没有短接或缺线，再打开仿真。
采样 LED 网络，检查是否依次循环；不要只凭 SVG 或编译结果下结论。
```

典型调用顺序：

```text
proteus_doctor
  → example_led_chaser
  → project_validate
  → firmware_build
  → native_apply
  → native_read
  → project_open
  → simulation_control
  → simulation_sample
```

`native_apply` 返回新文件的路径。它不是“把当前打开的电路直接改掉”；实时编辑请使用 `live_read` / `live_edit`。参数和可复制示例见[示例文档](docs/examples.md)。

## 工作方式

```text
MCP 客户端
    │ stdio / JSON-RPC
    ▼
Proteus MCP
    ├─ 语义模型、版本检查、C51 编译、证据文件
    ├─ 本机器件库 / 原生工程读写
    └─ 版本锁定的原生桥 → MCP 启动的 Proteus 会话
```

语义编辑使用 `expected_revision`；文件修改使用 SHA-256；实时编辑和 PCB 路由使用最新读取返回的 token。工具不会接管用户自行打开的 Proteus 进程，也不会强杀进程或自动关闭未知弹窗。

**原生实时编辑不是原子事务。** `partial`、`unconfirmed` 或超时不等于“什么都没改”。发生这些结果后应重新读取并检查证据，不要直接重试。操作流程见[工具参考](docs/tools.md#版本与结果)。

## 当前边界

尚不支持异构多单元器件、四层及以上 PCB、盲埋孔、通用过孔/分支推挤、复杂覆铜和禁布区、PCB 封装放置与规则编辑、通用仪器控制、模拟电流采样，以及 Gerber/ODB++/贴片坐标输出。

SVG 是重建预览，不是 Proteus 窗口截图。数字网络采样不等于 LED 光学亮度、蜂鸣器音频、完整波形或实板测试。布线拓扑检查也不能代替最终的原生画面排版验收。

## 开发与测试

```powershell
python scripts/run_tests.py --profile portable
python scripts/check_release.py
python -m pip install build
python -m build
python scripts/check_release.py --artifacts dist
```

`portable` 明确跳过依赖本机商业库和私有校准工程的测试。完整本机测试和原生验收是另外两层检查，范围和结果见[测试说明](docs/testing.md)。

欢迎提交可复现的问题和小范围 PR。扩展某个原生操作时，请同时提供版本校验、失败保护、保存重开和回读证据，而不只是菜单项或工具名称。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

项目代码采用 [MIT](LICENSE) 许可证。Proteus、Keil 及其模型、库、工程内嵌资源不属于本项目授权范围。Proteus MCP 是非官方项目，与 Labcenter Electronics、Arm/Keil 无隶属或背书关系。第三方说明见 [NOTICE.md](NOTICE.md)。
