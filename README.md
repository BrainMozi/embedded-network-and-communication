# 嵌入式网络与通信

课程项目仓库。各阶段独立保存，当前已完成阶段一：基于 QEMU、FreeRTOS 和 UDP 组播的简化车载网络实验。

## 阶段一

见 [`stages/stage-01/`](stages/stage-01/)：课程 PPT、任务书、实践报告、项目源码与一次实验的原始 PCAP、串口日志。工程构建和运行方式见 [`qemu_brake_system/README.md`](stages/stage-01/qemu_brake_system/README.md)。

工程在 Ubuntu 虚拟机中使用外部 FreeRTOS 内核和 FreeRTOS+TCP 导出源码；这些第三方源码未并入本仓库。阶段二及后续阶段开始时再增添对应目录，不预先放空模板。
