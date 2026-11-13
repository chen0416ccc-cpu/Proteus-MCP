# 参与贡献

欢迎先用 Issue 描述具体工作流、失败步骤和期望结果。请注明 Python、Windows、Proteus 的版本，以及 `proteus_doctor` 中相关能力的状态。发布前删除用户名、机器名、个人路径、许可证、模型和不便公开的工程内容。

## 本地开发

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe scripts/run_tests.py --profile portable
```

原生开发再安装 `.[bridge]`，按[本机初始化](docs/calibration.md)自动准备资源；旧私有基线可显式覆盖。不要提交商业库或工程来消除测试的跳过项。公共 CI 不安装 Proteus 或 Keil。

## 新构建适配

1. 在独立工作区运行 `doctor`，保留 `native_build` 的实际模块哈希、架构和缺失能力。公开报告前移除本机路径及个人信息，不上传商业模块。
2. 分析目标构建的函数签名、调用约定、结构布局、虚表和事件循环要求。旧构建的固定偏移不能直接移用；相同版本号也不能代替模块校验。
3. 在 `build_profiles.py` 新增独立 profile 和 adapter ID，按能力列出完整模块集合。为不同 ABI 提供单独的桥资源，在 `ADAPTER_SOURCES` 注册；只有证据表明 ABI 相同时才共享资源。
4. 添加错误哈希、缺失模块、模糊匹配和跨 profile 混用的拒绝测试。不能仅将期望哈希换成新文件哈希就宣布支持。
5. 使用新适配器执行 `setup --verify-native` 和 `scripts/verify_native.py`，保留打开、保存重开、实际引脚/折点、参数和仿真采样的结果。修改源码后重新执行。该脚本不覆盖实时编辑、PCB 或其他新能力，这些能力还需各自的原生往返测试。
6. 只公开已验证的能力，更新兼容表及限制。某项能力通过不自动放开其余能力；保留未知构建拒绝路径。

## 提交要求

- 一个 PR 解决一类可说明的问题。优先添加失败回归，再改行为。
- 新工具更新 `server.py` 的 schema、`service.py` 分派、能力边界和[工具参考](docs/tools.md)。工具的名称不是原生执行证据。
- 原生适配必须校验构建、文档身份、输入、版本和拓扑；失败返回应区分 `partial`、`unconfirmed` 与确定未发生变更。
- 不添加接管外来进程、强杀、绕过哈希保护或自动确认未知弹窗的捷径。
- 说明验证分层：单元测试、文件回读、原生保存重开、仿真采样、目视排版、实板测量不能互相替代。
- 执行 `python scripts/check_release.py`，检查暂存区中没有校准工程、个人数据、日志、二进制或凭据。

提交 PR 即表示你有权按项目 MIT 许可证贡献相应内容；第三方代码请保留来源和许可证说明。不要上传厂商文件作为附件。
