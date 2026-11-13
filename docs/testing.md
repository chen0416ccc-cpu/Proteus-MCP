# 测试与验收

## 不同层次的证据

| 层次 | 验证什么 | 不能证明什么 |
| --- | --- | --- |
| 可移植测试 | schema、模型、解析、版本冲突、路由算法、采样判定和保护分支 | 安装了 Proteus、原生绘图或仿真成功 |
| 本机完整单元测试 | 额外使用私有校准文件和本机库，检查实际记录/几何/导出 | 原生软件已经加载并正确保存 |
| 原生回归 | stdio 调用、打开/保存重开、真实几何、输入和数字采样、PCB 回读及崩溃诊断 | 所有原生功能覆盖、最终字体排版或实板验收 |
| 原生目视 | 实际窗口中的文字、线条、功能区和蓝框呈现 | 完整电气正确性、采样完整性或硬件行为 |

## 可移植测试与 CI

```powershell
python scripts/run_tests.py --profile portable
```

脚本通过 unittest 发现测试，显式跳过依赖商业库/私有模板的条目，最后输出包含 `collected`、`passed`、`skipped`、`failures`、`errors` 的 JSON。跳过项不计入通过数。测试不启动 Proteus。

GitHub Actions 在 Ubuntu 和 Windows 的 Python 3.11、3.13 上运行这一配置、源文件审计、构建、包审计及安装后 stdio 冒烟。查看[真实工作流结果](https://github.com/chen0416ccc-cpu/Proteus-MCP/actions/workflows/ci.yml)，不要从本地结果推断远程 CI 已通过。

## 本机完整单元测试

```powershell
python scripts/run_tests.py --profile full
```

该配置需要维护者测试集中的私有基线文件和相应库。在未配置环境运行时会失败，不会伪造 fixture 或自动下载商业资产。本机模板不随源码分发，普通用户安装不需要运行这一维护者配置。

## 原生回归

维护者原生回归使用本机私有 harness 和独立测试工程副本，部分输入来自有权使用的安装样例。因此 harness 的开发现场路径、样例和完整运行日志不随公开包分发。它与公开 portable CI 是独立验收，不能混为一谈。

本机完整报告保存于工作区 `_acceptance/<时间戳>/report.json`，记录全部工具响应、源码执行前后指纹、harness 指纹、子项、stderr、服务器退出码和清理状态。生产源码或桥资源变化后，旧报告只能作为历史记录。

`proteus_doctor.latest_acceptance` 仅在报告通过、退出码为 0、stderr 为空、源码指纹一致时显示 `verified_for_current_source`。没有本机报告时为 `not_recorded`，这是正常状态，不意味着已经测过或没有能力。

当前回归范围包括八灯 C51/实际灯序、默认完整导线和蓝框边界、每条已加载原生折线与文件匹配、保存重开、分支删除/撤销、同构多单元、原生 ERC、双层铜线/通孔/有界推挤、原生 DRC/预生产检查、BUTTON/LOGICSTATE、计数回绕和中断嵌套。它不是对整个 Proteus API 的覆盖率声明。

## 分发检查

```powershell
python scripts/check_release.py
python -m build
python scripts/check_release.py --artifacts dist
```

审计 Git 跟踪文件和 wheel/sdist 成员：拒绝私有目录、原生工程、厂商二进制、常见凭据、私人路径和意外成员；检查文档链接、53 个工具和桥资源；比较包内源码与当前 checkout。它是补充门槛，不是完整秘密扫描器或第三方许可证法律审查。

在新虚拟环境安装 wheel 后执行：

```powershell
python scripts/smoke_stdio.py --python D:\Projects\CleanEnv\Scripts\python.exe
```

冒烟脚本从临时目录、隔离模式启动已安装模块，完成 initialize、tools/list、doctor 和 ping，故意使用不存在的原生依赖路径，确认服务不会把缺依赖说成可用。它不打开 Proteus，也不会修改用户 MCP 配置。

## 结果解释

`native_verify` 默认判定校准的低有效循环灯序，不是任何电路的通用成功按钮。ERC/DRC 的调用状态和设计是否通过应分别读取。发生原生部分失败或超时后，先看回读与证据，不拼接不同失败轮的子项为一份成功报告。

最终发布仍明确保留目视边界：程序化折点和锚点一致，不能自动证明字距、文字遮挡和整张图的观感已经合格。数字采样也不是音频、光学或真实开发板实测。
