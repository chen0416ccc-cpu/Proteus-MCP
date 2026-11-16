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

通用绘图的可移植测试覆盖串联、分支、并联、桥式、独立组、RC、任意引用名、插入顺序、旋转/镜像、长标注、锚定坐标、小纸张拒绝，以及缺线、标签伪装断线、端点错位和穿体的负例。另固定 40 个随机种子生成非示例网络与不同朝向，重复检查通用路径。小型内存测试记录是原创合成数据，不含厂商模板。

`test_general_native` 另用本机真实 NE555、OPAMP、BC547、BUTTON 库定义生成非八灯电路，检查保存文件的连通关系、DSN/CDB、完整几何质量及实际纸张尺寸。这组单元测试只读写文件，不启动 Proteus。NE555 的原生加载另由下文脚本验证；其余电路不能由此推断已完成原生仿真。实时移动的拒绝分支由独立合成状态测试覆盖，不冒充桥端运行验收。

## 原生回归

源码提供无需屏幕操作的原生验收入口：

```powershell
python scripts/verify_native.py --workspace D:\ProteusAcceptance --case all
```

需要 Windows、兼容的 Proteus 安装、安装中的 Samples/Library、Keil C51 和 `[bridge]` 依赖。脚本先验证自动初始化，然后通过真实 stdio 服务生成 NE555 和八灯电路，核对已加载器件参数、引脚网络、导线折点与纸张尺寸，保存重开；八灯另检查实际 HEX 加载、数字灯序、暂停、继续和停止。没有截图、模拟输入或仿真 NE555 的通过判定。

`--case schematic` 仅检查初始化和 NE555 往返，不需要 Keil。`--case led` 检查初始化和八灯。用已安装 wheel 的 Python 执行脚本并传 `--installed-server` 时，服务以 `-I -m proteus_mcp` 在工作区启动，不从 checkout 导入服务代码；报告记录服务与 harness 各自的执行前后指纹。

既有的更广泛维护者回归仍使用私有 harness，部分输入来自安装样例，不随公开包分发。两类本机验收均独立于 portable CI。

本机完整报告保存于工作区 `_acceptance/<时间戳>/report.json`，记录全部工具响应、源码执行前后指纹、harness 指纹、子项、stderr、服务器退出码和清理状态。生产源码或桥资源变化后，旧报告只能作为历史记录。

`proteus_doctor.latest_acceptance` 仅在报告通过、退出码为 0、stderr 为空、源码指纹一致时显示 `verified_for_current_source`。没有本机报告时为 `not_recorded`，这是正常状态，不意味着已经测过或没有能力。

私有回归包含分支删除/撤销、同构多单元、原生 ERC、双层铜线/通孔/有界推挤、原生 DRC/预生产检查、BUTTON/LOGICSTATE、计数回绕和中断嵌套等独立项目。公开脚本的通过结果不包含这些项目，也不是整个 Proteus API 的覆盖率声明。

## 活动模型与联合观测

可移植测试 `test_active_instances` / `test_observations` 覆盖活动记录、隐藏名称、未知定义拒绝、物理连接归并、未知模拟信号、音频线程释放、错误留证、冷启动窗口与清理及身份冲突。音频单元测试使用替身，不代表本机播放已经成功。

显式运行隔离原生验收：

```powershell
python scripts/verify_observations.py --run-native --workspace D:\ProteusAcceptance\Joint --fixture D:\Fixtures\Interrupt.pdsprj
```

`--fixture` 只读取本机保存的单页外部中断电路，要求包含 U1、BZ1、D1..D8、SW1/SW2 及对应命名网络。脚本在新副本中使用生产 Builder 重建 BZ1 的活动记录，换入专用 C51 测试固件，核对源文件哈希、接线及几何不变；不修改原件。它验证首半秒采样、按键分派、计数/报警网络变化、播放电平、引脚/内部状态联合回读和正常停止/关闭。无需屏幕、截图、鼠标或键盘。

默认直接调用 Service 并校验生产 schema；使用已安装 wheel 的 Python 并加 `--installed-server` 后，工具通过隔离 stdio 服务执行，同时核对安装包与 checkout 的生产源码、服务前后指纹、stderr 及退出码。报告为工作区 `observations-acceptance.json`，原始样本在其中记录的 `evidence_path`。两种模式不能混称。

省略 `--fixture` 时尝试全新导出外部中断示例。当前已知该布局可能触发 `LABEL_SPACE_EXHAUSTED`，此路径未通过；不会放宽布局检查或静默回退。已有 fixture 上联合观测通过，不表示自动布局问题已修复，也不证明未经测试的活动器件可用。进程播放电平不等同人耳试听，内部灯态不等同画面光学验收。

## 分发检查

```powershell
python scripts/check_release.py
python -m build
python scripts/check_release.py --artifacts dist
```

审计 Git 跟踪文件和 wheel/sdist 成员：拒绝私有目录、原生工程、厂商二进制、常见凭据、私人路径和意外成员；检查文档链接、57 个工具和桥资源；比较包内源码与当前 checkout。新增文件须纳入审计索引；只审计旧的跟踪文件不能证明新文件已检查。它是补充门槛，不是完整秘密扫描器或第三方许可证法律审查。

在新虚拟环境安装 wheel 后执行：

```powershell
python scripts/smoke_stdio.py --python D:\Projects\CleanEnv\Scripts\python.exe
```

冒烟脚本从临时目录、隔离模式启动已安装模块，完成 initialize、tools/list、doctor 和 ping，故意使用不存在的原生依赖路径，确认服务不会把缺依赖说成可用。它不打开 Proteus，也不会修改用户 MCP 配置。

## 结果解释

`native_verify` 默认判定校准的低有效循环灯序，不是任何电路的通用成功按钮。ERC/DRC 的调用状态和设计是否通过应分别读取。发生原生部分失败或超时后，先看回读与证据，不拼接不同失败轮的子项为一份成功报告。

最终发布仍明确保留目视边界：程序化折点和锚点一致，不能自动证明字距、文字遮挡和整张图的观感已经合格。数字采样也不是音频、光学或真实开发板实测。
