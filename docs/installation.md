# 安装与客户端配置

## 先检查兼容性

本项目不是 Proteus 官方 SDK，也不依赖假定存在的 COM 接口。原生桥在本机 Proteus 进程内调用已校准的函数，执行前校验模块 SHA-256。当前只覆盖特定 Proteus 9 构建，详见[限制](limitations.md)。

使用独立工作区和工程副本。首次原生测试前，请先保存并正常退出自己打开的 Proteus；MCP 不接管外来进程。不要以管理员身份启动不需要提权的客户端。

## 从源码安装

要求 Python >= 3.11、Git、Windows PowerShell 5.1 或更新版本。

```powershell
git clone https://github.com/chen0416ccc-cpu/Proteus-MCP.git D:\Projects\Proteus-MCP
cd D:\Projects\Proteus-MCP
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[bridge]"
```

`bridge` 安装固定版本 `frida==17.26.2`。不需要原生控制时，可安装 `-e .`，不引入必选第三方依赖。

## 从 Release 安装

从本仓库的 [Releases](https://github.com/chen0416ccc-cpu/Proteus-MCP/releases) 下载 wheel 和 `SHA256SUMS.txt`，用 `Get-FileHash -Algorithm SHA256` 对照后安装：

```powershell
py -3 -m venv D:\Projects\ProteusEnv
D:\Projects\ProteusEnv\Scripts\python.exe -m pip install '.\proteus_mcp-0.1.0-py3-none-any.whl[bridge]'
```

wheel 不带仓库的 `scripts/`、测试和详细文档。需要准备模板校验脚本、参与开发或运行测试时，使用源码仓库或 sdist。未向 PyPI 发布，不要直接安装未经核对的同名包。

## 路径配置

所有路径请明确填写，不依赖为开发环境保留的 D 盘默认值。

| 环境变量 | 内容 |
| --- | --- |
| `PROTEUS_MCP_WORKSPACE` | 可写工作区；输出、会话记录、备份、日志和采样都在这里 |
| `PROTEUS_MCP_EXE` | Proteus 安装目录中的 `Bin\PDS.exe`，不是 `.lnk` |
| `PROTEUS_MCP_LIBRARY` | 实际 `.LIB` 所在目录，不是 `MODELS` 目录 |
| `PROTEUS_MCP_KEIL` | 包含 `C51\BIN` 的 Keil 根目录 |
| `PROTEUS_MCP_CALIBRATION` | 本机私有校准工程的绝对路径；仅新建/导出原生文件需要 |
| `PROTEUS_MCP_LAUNCHER` | 可选，覆盖自动选择的启动程序；仅在默认启动方式不适用时使用 |

除启动器和校准模板外，命令行也可传 `--workspace`、`--proteus`、`--library`、`--keil`。这些全局选项必须写在 `serve` / `doctor` / `call` 之前。

```powershell
$env:PROTEUS_MCP_EXE = 'C:\Program Files\Labcenter Electronics\Proteus 9 Professional\Bin\PDS.exe'
$env:PROTEUS_MCP_LIBRARY = 'C:\ProgramData\Labcenter Electronics\Proteus 9 Professional\Library'
$env:PROTEUS_MCP_KEIL = 'C:\Keil_v5'
$env:PROTEUS_MCP_WORKSPACE = 'D:\ProteusWork'
.\.venv\Scripts\python.exe -X utf8 -m proteus_mcp doctor
```

从源码启动也可以用 `Start-ProteusMcp.ps1 -Doctor`；脚本优先使用仓库的 `.venv`，不修改全局 Python 或客户端配置。`Start-ProteusMcp.ps1` 的 `-Workspace` 参数优先于环境变量，省略时默认使用仓库的 `workspaces/`。

## Codex

根目录的 [mcp-config.example.toml](../mcp-config.example.toml) 提供完整示例。将路径改成自己的安装位置再添加到客户端配置。使用 wheel 时，将其中的 `run_server.py` 改为 `args = ["-X", "utf8", "-m", "proteus_mcp", "serve"]`，并填写安装该 wheel 的 Python 路径。

## 其他客户端

支持 stdio 的客户端可使用以下 JSON 结构，具体配置文件位置以客户端文档为准：

```json
{
  "mcpServers": {
    "proteus": {
      "command": "D:\\Projects\\Proteus-MCP\\.venv\\Scripts\\python.exe",
      "args": ["-X", "utf8", "-m", "proteus_mcp", "serve"],
      "env": {
        "PROTEUS_MCP_WORKSPACE": "D:\\ProteusWork",
        "PROTEUS_MCP_EXE": "C:\\Program Files\\Labcenter Electronics\\Proteus 9 Professional\\Bin\\PDS.exe",
        "PROTEUS_MCP_LIBRARY": "C:\\ProgramData\\Labcenter Electronics\\Proteus 9 Professional\\Library",
        "PROTEUS_MCP_KEIL": "C:\\Keil_v5"
      }
    }
  }
}
```

准备好[模板](calibration.md)后，把 `PROTEUS_MCP_CALIBRATION` 添加到 `env`。重启客户端中的 MCP 服务，再调用 `proteus_doctor`。此配置结构不代表每一种客户端都已经实测。

## 常见问题

**能列出工具，但运行不了原生操作。** 工具列表是静态接口，不是后端可用性证明。检查 `doctor.capabilities` 中对应条目的 `available` 和 `reason`。

**`NATIVE_BUILD_NOT_CALIBRATED`。** 安装文件和已校准构建不一致。不要改哈希来跳过；需要为该构建重新定位、校准和验收适配器。

**缺少校准工程。** GitHub 包刻意不携带商业资源。按[模板准备](calibration.md)完成本机设置，或只使用不依赖模板的功能。

**提示可选桥依赖缺失。** 在客户端使用的那个 Python 环境安装 `.[bridge]` 或 Release wheel 的 `[bridge]` 扩展。旧版诊断中的 `research/deps` 是本地回退提示，不是公开安装步骤。

**工程启动被阻止。** 保存并退出外来 Proteus 进程，或者使用 MCP 返回的自有 `session`。不要删除会话记录或强杀进程来规避保护。

**弹窗、保存未确认或超时。** 先读 `native_status` / `runtime_diagnostics` 并保留证据。未知弹窗不会自动确认；实时编辑出现异常后先回读，不能盲目重试。
