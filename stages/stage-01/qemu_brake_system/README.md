# 第二版 QEMU 车载 IoT 网络仿真工程

## 结构

```text
qemu_brake_system/
├── network_firmware/      ARM 固件源码、配置和构建文件
├── scripts/               虚拟网络建立与清理脚本
├── run_experiment.py      启动控制器、四个 ECU、抓包并采集日志
├── analyze_results.py     解析日志和 PCAP，生成统计图
├── generate_evidence.py   在 Windows 使用 TShark 核验 PCAP 与 ECU 序号
└── runtime/run_01/        本次运行的原始证据
```

## 节点

```text
控制器：10.10.0.10
ECU-1：10.10.0.11（实战一重点分析对象）
ECU-2：10.10.0.12
ECU-3：10.10.0.13
ECU-4：10.10.0.14
```

四个 ECU 平级，均处理相同的 UDP 组播指令。

```text
组播地址：239.255.0.1
UDP 端口：5000
```

## 在虚拟机中运行

```bash
cd /home/brain/qemu_brake_network
sudo ./setup_tap_network.sh
make ROLE=controller NODE_ID=0
make ROLE=ecu NODE_ID=1
make ROLE=ecu NODE_ID=2
make ROLE=ecu NODE_ID=3
make ROLE=ecu NODE_ID=4
sudo python3 run_experiment.py --duration 19 --name run_01
sudo python3 analyze_results.py runtime/run_01
```

上述是虚拟机中部署后的命令；Windows 工程目录内固件 Makefile 在 `network_firmware/`、建网脚本在 `scripts/`，运行时按部署目录调整路径。脚本启动 1 个控制器和 4 个 QEMU ECU，并用 Linux `tcpdump` 保存 PCAP。UDP 组播不保证每个 ECU 都收到全部报文；本次日志中 ECU-1/2/3/4 分别记录 11/12/13/11 条。历史 `runtime/run_01/metadata.json` 曾把 ECU 地址写高一位，应以固件地址公式 `10.10.0.(10+NODE_ID)`、PCAP 和此表为准。ECU-1 的串口日志用于实战一调度分析。

## 任务优先级

```text
应用任务：刹车 8 > 网络 6 > 普通 4 > 传感器 3 > 心跳 2 > 日志 1
```

日志任务不调用 `vTaskDelay`，持续运行并被高优先级任务抢占；心跳周期运行；传感器和控制指令具有随机间隔。

## Windows Wireshark

将 `runtime/run_01/traffic.pcap` 复制到 Windows 后，用 Wireshark 打开并手动分析：

```text
udp.port == 5000
```

```text
ip.dst == 239.255.0.1 && udp.dstport == 5000
```

Linux 负责仿真、采集和 PCAP；Windows Wireshark 负责老师要求的图形化查看、统计和截图。
