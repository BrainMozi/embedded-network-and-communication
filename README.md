# 嵌入式网络与通信

课程项目仓库，按阶段归档；当前已完成两个阶段。

## 项目进度

- **阶段一：嵌入式网络基础** — QEMU、FreeRTOS+TCP、UDP 组播与多 ECU 任务调度。材料、源码和运行证据见 [`stages/stage-01/`](stages/stage-01/)；构建与运行方式见 [`qemu_brake_system/README.md`](stages/stage-01/qemu_brake_system/README.md)。
- **阶段二：车载网络与协议** — Python、python-can、SocketCAN 虚拟 CAN 灯光控制。课件、实践报告、源码与实测证据见 [`stages/stage-02/`](stages/stage-02/)。

阶段二在 Ubuntu 中通过 VS Code 任务启动：打开 `stages/stage-02/can_lighting_demo/`，选择 `.venv/bin/python`，按 **Ctrl+Shift+B**。本阶段使用 `vcan0` 软件仿真，不连接实体 CAN 适配器或真实灯具；LIN、车载以太网/TSN 和 AUTOSAR 未纳入本阶段实现。