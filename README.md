# Proteus MCP

[![CI](https://github.com/chen0416ccc-cpu/Proteus-MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/chen0416ccc-cpu/Proteus-MCP/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

用于 Proteus 的 MCP 服务，提供器件库查询、原理图编辑、PCB 布线和仿真控制工具。通过本地 stdio 接入支持 MCP 的 AI 客户端。

> 项目处于实验阶段。原生功能仅适配[指定的 Proteus 9 构建](docs/limitations.md#兼容范围)；导出新原生工程需要准备[本机校准模板](docs/calibration.md)。

[安装](#安装) · [客户端配置](#客户端配置) · [使用示例](#使用示例) · [工具参考](docs/tools.md) · [问题反馈](https://github.com/chen0416ccc-cpu/Proteus-MCP/issues)

## 功能

| 功能 | 支持内容 |
| --- | --- |
| 器件库 | 搜索本机 `.LIB`，查看物理引脚、隐藏电源和器件属性 |
| 原理图 | 器件放置、旋转与镜像、自动布局、正交连线、汇接点及 `.pdsprj` 导出 |
| 当前工程 | 在 MCP 启动的会话中添加、连接、移动和删除器件，修改属性，撤销与重做 |
| PCB | TOP/BOT 双层布线、通孔换层，以及已校准路径的有界推挤 |
| 检查与输出 | 原生 ERC、物理间距 DRC、预生产检查和 CSV/JSON BOM |
| 固件与仿真 | Keil C51 编译、仿真启停、数字网络采样、BUTTON/LOGICSTATE 激励和四位数码管解码 |

完整接口见[工具参考](docs/tools.md)，器件、布线和仿真的支持范围见[兼容性与限制](docs/limitations.md)。

## 安装

- Python 3.11 或更新版本。
- 原生功能需要 Windows 10/11 和兼容的 Proteus 安装。
- 编译 C51 程序时需要 Keil C51。

在 PowerShell 中执行：

```powershell
git clone https://github.com/chen0416ccc-cpu/Proteus-MCP.git D:\Projects\Proteus-MCP
cd D:\Projects\Proteus-MCP
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[bridge]"
```

仅使用器件库查询和语义模型时，可将安装命令中的 `".[bridge]"` 改为 `.`。Release wheel 安装方法见[安装文档](docs/installation.md#从-release-安装)。

## 客户端配置

以 Codex 的 `config.toml` 为例，将路径改为本机安装位置：

```toml
[mcp_servers.proteus]
command = 'D:\Projects\Proteus-MCP\.venv\Scripts\python.exe'
args = ["-X", "utf8", "-m", "proteus_mcp", "serve"]

[mcp_servers.proteus.env]
PROTEUS_MCP_WORKSPACE = 'D:\ProteusWork'
PROTEUS_MCP_EXE = 'C:\Program Files\Labcenter Electronics\Proteus 9 Professional\Bin\PDS.exe'
PROTEUS_MCP_LIBRARY = 'C:\ProgramData\Labcenter Electronics\Proteus 9 Professional\Library'
# 编译 C51 程序时配置：
# PROTEUS_MCP_KEIL = 'C:\Keil_v5'
# 导出新原生工程时配置：
# PROTEUS_MCP_CALIBRATION = 'D:\ProteusLocal\template.pdsprj'
```

准备好校准模板后，取消对应配置行的注释并填写文件路径。配置完成后启动 MCP 服务，调用 `proteus_doctor` 检查依赖和兼容性。

Claude Desktop 等 stdio 客户端的 JSON 配置见[客户端配置](docs/installation.md#其他客户端)。

## 使用示例

**查询器件**

```text
在本机器件库中查找 NE555，列出它的物理引脚、默认参数和所在库。
```

**绘制电路**

```text
新建一个 NE555 无稳态振荡器，定时电阻为 10 kΩ 和 100 kΩ，定时电容为 10 µF。
按定时网络和输出负载分区布局，导出 Proteus 工程并打开。
```

**查看连接**

```text
读取当前 MCP 会话的电路，列出 U1 各引脚所连接的网络和器件。
```

**运行仿真**

```text
运行已打开工程的仿真，采样 OUT 网络 10 秒，显示数字电平的变化。
```

工具调用参数、C51 流水灯和外部中断示例见[示例文档](docs/examples.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [安装与配置](docs/installation.md) | 环境变量、客户端配置和常见问题 |
| [校准模板](docs/calibration.md) | 新建原生工程所需的本机模板 |
| [工具参考](docs/tools.md) | 参数、返回值和会话操作 |
| [布局与布线](docs/drawing.md) | 自动布局、固定位置、纸张与连线检查 |
| [兼容性与限制](docs/limitations.md) | 支持的构建、器件、PCB 和仿真范围 |
| [架构](docs/architecture.md) | 文件格式、原生桥和模块结构 |

## 开发与测试

```powershell
python scripts/run_tests.py --profile portable
python scripts/check_release.py
```

测试环境见[测试说明](docs/testing.md)，提交改动前请阅读[贡献指南](CONTRIBUTING.md)。

## 许可证

代码采用 [MIT](LICENSE) 许可证。Proteus、Keil 及其商业资源需另行获取，不包含在本仓库中。

Proteus MCP 为非官方项目，与 Labcenter Electronics、Arm/Keil 无隶属关系。第三方说明见 [NOTICE.md](NOTICE.md)。
