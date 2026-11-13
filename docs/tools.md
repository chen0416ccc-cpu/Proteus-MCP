# 工具参考

共 53 个工具。以下列出用途和关键参数，完整 JSON Schema 以 MCP 的 `tools/list` 返回为准。不能把“能发现命令”理解为“已验证执行该命令”。

## 版本与结果

- 语义模型变更传当前 `expected_revision`，每批成功修改后重新读取修订号。
- 原生文件修改传 `expected_sha256`，输出到新文件；不要把修改前的哈希用于下一次写入。
- 当前会话变更先读 `live_read` / `pcb_read`，使用本次返回的 `expected_token`。一个 token 不能跨结构修改反复使用。
- 仿真输入 token 还绑定本次仿真 epoch、上下文和输入对象；停止/重启后重新调用 `simulation_inputs`。
- `planned`、`compiled`、`generated_unverified`、`dispatched` 都不是完整验证成功。`partial`、`unconfirmed`、`busy`、`failed`、`error` 必须按真实状态处理。
- 原生实时/PCB 变更非原子，失败可能已经应用部分内容。先回读、保留证据，不自动重试，不将撤销当作可靠事务回滚。

## 诊断与发现

| 工具 | 用途与参数 |
| --- | --- |
| `proteus_doctor` | 依赖、路径、模块哈希、逐项能力和当前源码的本机验收记录。无参数。 |
| `runtime_status` | 进程、窗口和菜单元数据，可传 `session`。不截屏、不模拟输入。 |
| `runtime_diagnostics` | `session` 的崩溃事件、转储路径和采集错误，保存诊断文件。 |
| `native_status` | 自有 `session` 的原生命令、弹窗、日志及仿真状态。 |
| `native_commands` | 发现命令 ID、标签、模块和启用状态；`session` 必填，可按 `query` / `module` 过滤，不暴露任意命令执行。 |

## 器件库

| 工具 | 用途与参数 |
| --- | --- |
| `catalog_list` | 支持的逻辑器件及引脚。可选 `device` 和 `library_path`。不代表已放置。 |
| `library_search` | 搜索本机真实 `.LIB` 索引，传 `query`，可选 `limit`。 |
| `library_device` | 读取 `device` 的物理引脚、几何、属性和来源。重名时指定 `library_path`，支持的同构多单元需显式 `unit`。 |

## 语义电路

这些工具编辑工作区模型，不直接改变当前 Proteus 窗口。

| 工具 | 用途与参数 |
| --- | --- |
| `project_create` | 新建 `project` 目录，不覆盖同名项目。项目名只能用 ASCII 字母、数字和下划线，首位为字母。 |
| `project_read` | 读取 `project` 的模型、修订号和源码哈希。 |
| `component_add` | `project`、`expected_revision`、`ref`、`device`、`position`，可带属性、旋转、镜像和来源库。 |
| `component_set` | 同上版本参数，更新 `ref` 的 `properties`，可修改位置、旋转和镜像。仅改位置时传空属性对象。 |
| `circuit_edit` | 修订号检查的批量 `operations`：add / set / remove / connect / disconnect / merge_nets；预检失败整批不落盘。 |
| `connect_pins` | 为 `name` 网络连接 `pins`，例如 `U1.P1.0` 与 `D1.K`。拒绝隐式合并已有不同网络。 |
| `project_validate` | 检查引脚、基本电源/复位和悬空无源器件，不是原生 ERC。 |
| `project_plan` | 导出 `plan.json`，状态仍是计划。 |
| `export_svg` | 导出语义预览，不是 Proteus 截图。 |
| `export_netlist` | 导出便携语义 JSON，不是可直接导入 Proteus 的原生网表。 |

## 示例与固件

| 工具 | 用途与参数 |
| --- | --- |
| `example_led_chaser` | 新建 `project`，12 MHz/12T AT89C51、P1 低有效八灯、复位/晶振和 C51 源码。示例模型保留预设位置；自动原生导出与其他电路使用同一布局算法。 |
| `example_external_interrupt` | 新建 `project`，`mode=level` 或 `nesting`，可设 `initial_count=0..9999` 做边界测试，返回布局计划。 |
| `source_write` | 写 `main.c`。已有源码须传匹配的 `expected_sha256`，旧版备份。 |
| `firmware_build` | 实际调用 C51 / BL51 / OH51，保留日志并检验 HEX。不代表仿真通过。 |
| `firmware_inspect` | 读取 `path`，检查 Intel HEX 校验和、地址、重叠和复位向量。 |

## 原生文件

| 工具 | 用途与参数 |
| --- | --- |
| `native_inspect` | 读取 `path` 的 ZIP 成员与哈希，不做电气认证。 |
| `native_read` | 读取已支持的 `.pdsprj`：器件、属性、引脚连接、真实导线几何和 DSN/CDB 一致性。 |
| `native_preview` | 根据实际文件几何重建 SVG；`path` 必填，可指定新的 `output`。仍不是原生窗口截图。 |
| `native_create` | 在已有语义 `project` 下生成新的实验性空工程，需模板并单独保存重开。 |
| `native_set_component` | `path`、新 `output`、`ref`、`properties`、`expected_sha256`，同步修改支持的 DSN/CDB 字段。 |
| `native_export_led_chaser` | 严格检查八灯例拓扑，调用带完整信号线和边界检查的新文件导出。 |
| `native_export` | 从 `project` 导出新文件。支持 `layout`、`routing`、`placements`、`sheet_size` 和显式 `wire_plan`，不覆盖原件。 |
| `native_apply` | 使用默认布局/布线导出新文件的便捷入口，不是当前会话原位写入。默认完整信号线，禁止静默退回标签短线。 |

`layout=auto` 按网络图、引脚方向和器件/全文标注尺寸生成三个候选方案，比较线长、折点、交叉和占用面积。`routing=auto` 和 `schematic` 使用完整连续信号线，不走示例专用坐标。`routing=labels` 必须显式选择；`wires` 是旧版整格正交路由。所有模式和显式 `wire_plan` 共用质量检查，并从最终文件再次核对。

未指定 `sheet_size` 时，自动布局可扩大实际纸张；指定尺寸后不擅自放大。`layout=model` 不移动模型坐标，`placements` 中的锚定器件也不会被搬走。不满足检查就报错，不降级成标签散图。返回的 `routing.quality`、`routing.file_readback_quality` 和 `routing.layout_attempts` 用于检查与诊断，字段和边界见[布局与布线](drawing.md)。

`placements` 为原生整数格坐标，`wire_plan` 中折点和文字锚点为精确半格坐标。语义 `position` 与原生坐标的 Y 方向不同，见[架构](architecture.md)。文字宽高范围 0.4..4 格，旋转为 0/90/180/270 度，隐藏文字不移除网络身份。

## 工程生命周期

| 工具 | 用途与参数 |
| --- | --- |
| `project_open` | 直接打开 `path`，返回 `session`。传同一自有 session 可复用窗口或安全切换工程。可设 `wait_seconds=1..30`。 |
| `project_save` | 原生保存并回读核验，保留备份；PCB 保存还需重开确认磁盘铜层。 |
| `project_reopen` | 保存、正常关闭、重开，并比较结构。失败不继续下一步。 |
| `project_close` | 自有 `session` 正常关闭，不强杀、不处理未知未保存弹窗。 |
| `native_verify` | 打开/保存/重开/回读/运行/采样/停止/关闭的组合验收；默认针对校准的低有效循环灯序，不是任意电路的通用判分器。 |

## 当前原理图

| 工具 | 用途与参数 |
| --- | --- |
| `live_read` | 读取自有 `session` 当前 ISIS 页的器件、物理引脚、变换、导线、折点和 token。 |
| `live_edit` | `session`、`expected_token` 和 `operations`，支持 add / transform / set / connect；删除必须单独批次。仿真需停止。 |
| `live_history` | `action=undo` / `redo`，只操作同一文档已记录的 MCP 批次；外部修改、部分失败和历史分歧使其失效。 |

实时坐标使用 `position_nm`，不是语义 `position`。连接操作为 `{op:connect, from_ref, from_pin, to_ref, to_pin}`；删除器件时必须明确是否删除直连线。单支路用 `delete_wire` 的精确地址，DOT 用 `delete_junction` 并列出全部入射导线地址。不提供推测整个网络范围的删除。

对已观测的接线器件，变换前按原生端点更新方式做预检。会留下斜线或零长段的移动返回 `LIVE_TRANSFORM_REQUIRES_REROUTE`，没有实现的实时重布线不会被说成成功。实时回执的 `geometry_verified` 只覆盖引脚/导线链接、端点和正交线段，`layout_quality_verified` 仍为 `false`，不等同于离线导出的整图检查。

## PCB 与规则

| 工具 | 用途与参数 |
| --- | --- |
| `pcb_read` | 导出并读取自有 ARES 板的 pad id、网络、层、边界、规则、铜线和通孔，返回 token 与可用路径清单。 |
| `pcb_route` | 真实 TOP/BOT 同层铜线，传 `net`、`layer`、`from_pad`、`to_pad` 和当前 token。可给 `points_nm` 或保守 A*。 |
| `pcb_route_layers` | TOP/BOT 换层和全通孔，使用真实 pad id、via style；可给显式 waypoints 或自动分层路径。 |
| `pcb_route_push` | 单条孤立 MCP 路径的有界推挤；默认 `mode=plan`，应用须显式 `apply` 和授权 `movable_wire_ids`。 |
| `pcb_route_push_chain` | 最多八条明确授权 MCP 路径的有界连锁位移，固定 pad 锚点，应用后精确回读和新鲜原生 DRC。 |
| `native_check` | `kind=erc` 使用最新 live token；`drc` / `preproduction` 使用最新 pcb token。检查完成与 `passed` 分开判断。 |
| `manufacturing_bom` | 对已保存且一致的 `path` 与 `expected_sha256` 输出 CSV/JSON BOM，物理封装去重，未分配项单列。 |

PCB 坐标、宽度、间距为整数纳米，10 nm 对齐。只处理[明确支持的几何和双层范围](limitations.md#pcb)。pad id 来自 `pcb_read`，不能把原理图的 `U1.1` 当作 ARES pad id。

## 仿真与观测

| 工具 | 用途与参数 |
| --- | --- |
| `simulation_control` | 自有 `session` 中 `action=run` / `pause` / `stop`，检查真实命令状态。 |
| `simulation_log` | 读取 Proteus 原生日志模型中的行，不从截图 OCR 猜测。 |
| `simulation_sample` | 按 `nets` 采样 DSIM 状态，保留强度位和原始证据，适合循环灯序检查。 |
| `simulation_inputs` | 运行会话的 BUTTON / LOGICSTATE 清单与本 epoch 的输入 token。 |
| `simulation_stimulate` | 当前输入 token 加 `events`，按原生仿真时间发送事件并记录迟发和原始采样。BUTTON 的 1=按下，0=释放；LOGICSTATE 的 1=高，0=低。 |
| `simulation_observe` | 指定 `nets`、`wall_ms` 和采样间隔，读取带仿真时钟边界的数字向量，保存完整原始数据。 |
| `simulation_decode_display` | `trace_path`、`expected_sha256`、有序段线/位选线和极性，输出完整复合帧、确认次数与拒绝样本。 |

网络名称必须来自实际原生电路。采样、事件分派和数码管数字解码不代表完整波形、模拟电流、音频、光学或真实硬件验收。
