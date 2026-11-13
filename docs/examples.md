# 使用示例

下面的 JSON 是工具参数，不是整条 JSON-RPC 消息。用 MCP 客户端调用对应工具，并从真实返回值中读取路径、session、修订号和 token，不要把示例占位符当作有效值。

## 八路流水灯

先运行 `proteus_doctor`。本机路径、校准模板和版本锁定的桥均可用后，依次调用：

| 工具 | 参数 |
| --- | --- |
| `example_led_chaser` | `{"project":"led_demo"}` |
| `project_validate` | `{"project":"led_demo"}` |
| `firmware_build` | `{"project":"led_demo"}` |
| `native_apply` | `{"project":"led_demo"}` |

示例是 12 MHz、12T、P1 低有效八灯。默认固定布局中，LED 和限流电阻成列，八路信号使用有序的完整通道，复位、晶振和去耦不再散落到纸张下方。输出仍需原生打开和保存重开检查。

取 `native_apply` 返回的 `path`：

```json
{"path":"<上一步返回的 pdsprj 绝对路径>"}
```

将以上参数分别用于 `native_read` 和 `project_open`。前者检查 `database_consistent`、`stored_connectivity`、`wire_geometry` 和 `nonorthogonal_wire_segments`；后者返回自有 `session`。

```json
{"session":"<project_open 返回的 session>","action":"run"}
```

调用 `simulation_control` 启动后，用 `simulation_sample` 检查真实网络：

```json
{
  "session":"<当前 session>",
  "nets":["LED1_K","LED2_K","LED3_K","LED4_K","LED5_K","LED6_K","LED7_K","LED8_K"],
  "seconds":8,
  "interval_ms":20
}
```

低有效时应同一时刻只有一路阴极为低，按 P1.0 到 P1.7 循环。原始状态包含驱动强度，不要直接拿原始整数与 0/1 作未经校准的比较。结束时先停止，再保存和重开；也可以使用针对循环灯序的 `native_verify` 自动完成这组验收。

## 外部中断与四位计数

创建低电平触发例：

```json
{"project":"irq_level","mode":"level","initial_count":0}
```

调用 `example_external_interrupt` 后，编译 `firmware_build`。原生导出时保留示例返回的 `routing`、`placements`、`sheet_size` 和 `wire_plan`，不要将精心安排的通道丢掉后改用默认排列：

```text
native_export({
  project: "irq_level",
  routing: example.routing,
  placements: example.placements,
  sheet_size: example.sheet_size,
  wire_plan: example.wire_plan
})
```

这里是参数映射示意，不是可直接粘贴执行的 JavaScript。`mode=nesting` 创建 INT0 低优先级、INT1 高优先级的对照。`initial_count=9998` 用于明确测试 9998 → 9999 → 0000，不应替代正常初值 0000 的作业交付。

打开、保存重开并启动后，调用 `simulation_inputs` 取得 token；再调用 `simulation_stimulate`：

```json
{
  "session":"<当前 session>",
  "expected_token":"<simulation_inputs 返回的 token>",
  "events":[
    {"ref":"SW1","value":1,"at_ms":0},
    {"ref":"SW1","value":0,"at_ms":50}
  ],
  "nets":["INT0","INT1","ISR0_ACTIVE","ISR1_ACTIVE","COUNT_EVENT","WRAP_EVENT","BUZZER_DRIVE"]
}
```

BUTTON 的 1 表示按下，0 表示释放。时序基于原生仿真时间，不是硬实时保证。保留迟发、分派和网络电平证据；事件“已分派”不能直接证明中断已响应。数码管完整帧需用 `simulation_observe` 的原始 trace 和 `simulation_decode_display` 独立判定。

## 修改当前原理图

先停止仿真，再 `live_read`。例如只修改 R1 的值：

```json
{
  "session":"<当前 session>",
  "expected_token":"<最新 live_read token>",
  "operations":[{"op":"set","ref":"R1","properties":{"VALUE":"470"}}]
}
```

用 `live_edit` 调用。成功后重新读取，随后 `project_save` / `project_reopen` 核对磁盘结果。位置变换用 `position_nm`，连接用真实物理引脚号，不混用语义坐标或逻辑别名。

删除一条分支时，从当前 `live_read.state.wires` 选择精确 `wire_address`，单独提交删除批次。不以网络名猜测整个删除范围，也不使用旧地址重复操作。

## PCB 路径

打开自有的板工程副本，运行 `pcb_read`。路由参数中的 `from_pad`、`to_pad`、网络名和 via style 必须取自本次原生读取，不使用臆造的示例 pad。先对有界推挤使用 `mode=plan`，确认移动范围后再显式 `apply`。

应用后要求新 token、精确铜层/过孔回读和新鲜原生 DRC。保存不等于磁盘铜层验收，最后调用 `project_reopen` 检查 `pcb_disk_roundtrip_verified`。未知几何、高层板或复杂覆铜应明确停止，见[支持范围](limitations.md#pcb)。

## 布局要求

默认流程不再在路由失败后悄悄换成标签短线。显式 `routing=labels` 仅用于确实需要网络标签的电路，不能用来掩盖完整布线失败。所有导出都限制在纸张蓝框中；显式计划还审计非目标网络交叉和器件接触。

这些是结构检查，不是“任意电路自动美化”的保证。密集电路应安排功能区、对齐器件、分配信号通道，并在 Proteus 原生画面检查文字可读性。不要以 SVG、拓扑一致或 `passed` 子项替代最后的视觉检查。
