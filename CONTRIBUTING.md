# 参与贡献

欢迎先用 Issue 描述具体工作流、失败步骤和期望结果。请注明 Python、Windows、Proteus 的版本，以及 `proteus_doctor` 中相关能力的状态。发布前删除用户名、机器名、个人路径、许可证、模型和不便公开的工程内容。

## 本地开发

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe scripts/run_tests.py --profile portable
```

原生开发再安装 `.[bridge]`，按[校准说明](docs/calibration.md)配置自己的安装和模板。不要提交商业库或工程来消除测试的跳过项。公共 CI 不安装 Proteus 或 Keil。

## 提交要求

- 一个 PR 解决一类可说明的问题。优先添加失败回归，再改行为。
- 新工具更新 `server.py` 的 schema、`service.py` 分派、能力边界和[工具参考](docs/tools.md)。工具的名称不是原生执行证据。
- 原生适配必须校验构建、文档身份、输入、版本和拓扑；失败返回应区分 `partial`、`unconfirmed` 与确定未发生变更。
- 不添加接管外来进程、强杀、绕过哈希保护或自动确认未知弹窗的捷径。
- 说明验证分层：单元测试、文件回读、原生保存重开、仿真采样、目视排版、实板测量不能互相替代。
- 执行 `python scripts/check_release.py`，检查暂存区中没有校准工程、个人数据、日志、二进制或凭据。

提交 PR 即表示你有权按项目 MIT 许可证贡献相应内容；第三方代码请保留来源和许可证说明。不要上传厂商文件作为附件。
