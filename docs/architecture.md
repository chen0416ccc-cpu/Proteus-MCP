# 实现结构

## 三类状态

语义电路存储在工作区每个项目的 `circuit.json`，包含器件、网络和修订号。`component_add`、`circuit_edit` 等只改变这个模型；模型更新后原生状态标为未应用。

离线文件层读取真实安装库和 `.pdsprj` 中已校准的 DSN/CDB。原生导出写到新的时间戳目录，保存 manifest、源码和固件副本，再回读引脚拓扑。新文件不能只改 DSN 而放任数据库不一致。

运行时层记录 MCP 自己启动的进程、创建时间、工程路径和窗口身份，使用 Frida 原生桥在相应事件循环调用已校准功能。实时原理图、PCB 和仿真读取真实内存状态，不依靠鼠标坐标或 OCR。

## 坐标与布线

基线原生格距为 `GRID = 254000` nm。语义位置转换到原生位置时 X 除以 10，Y 除以 10 后取反。`placements` 是整数原生格；显式通道计划支持半格。`live_edit` 和 PCB 工具使用纳米，不能混用。

默认导出使用连续信号线，不再静默降级为短线网络标签。流水灯有专用的单调平行通道；复杂实验可以传显式通道计划。不同网络只允许经过审计的直线内部垂直交叉，不允许在端点、折点或 DOT 处误接。

导出后独立比较模型拓扑与保存的引脚引用。纸张检查覆盖符号、折点、端子和文字锚点，不宣称完成字体像素级碰撞判断。SVG 是重建几何的辅助输出，不能作为 Proteus 窗口截图。

## 模块

| 模块 | 责任 |
| --- | --- |
| `server.py` / `service.py` | stdio JSON-RPC、工具 schema、依赖和分派 |
| `model.py` / `catalog.py` | 修订、批量模型编辑、基础验证 |
| `library.py` / `devices.py` | 库索引、真实定义、来源哈希和分服务目录作用域 |
| `native9.py` / `database.py` / `native_backend.py` | 校准文件记录、DSN/CDB 一致性与新文件写入 |
| `schematic.py` / `orderly.py` / `led_layout.py` | 精确半格树、显式通道、紧凑流水灯布局 |
| `runtime.py` / `bridge.py` / `*_agent.js` | 进程归属、版本校验、原生调用和回读 |
| `live.py` / `pcb*.py` | 实时原理图、历史、有界 PCB 路由与推挤 |
| `checks.py` / `preproduction.py` / `manufacturing.py` | 原生检查报告与已保存工程 BOM |
| `simulation.py` / `display.py` / `verification.py` | 输入事件、原生时钟、数字采样及独立判定 |

## 冲突和失败

语义批量编辑在完整预检后原子写文件；原生调用无法提供相同保证。每次读取产生 token，写入前再次核验。原生操作后比较器件/网络/导线投影，保留非目标内容；保存重开另做磁盘验证。

`partial`、`unconfirmed` 和超时必须保留“可能已应用”的事实。原生撤销重做只覆盖匹配的已记录 MCP 批次，不是任意文档事务。当前已校准边界见[限制](limitations.md)。
