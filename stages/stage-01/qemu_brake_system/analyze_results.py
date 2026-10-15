#!/usr/bin/env python3
"""Analyze four-ECU serial logs and Linux PCAP; output SVG and JSON."""
from __future__ import annotations
import argparse,json,re,subprocess
from collections import Counter
from pathlib import Path
KV=re.compile(r"([A-Za-z_]+)=([^\s]+)")

def read_events(path):
    result=[]
    if not path.exists(): return result
    for line in path.read_text(errors="replace").splitlines():
        d={k:v for k,v in KV.findall(line)}
        for k in ("seq","tick","rx_tick","prio"):
            if k in d:
                try:d[k]=int(d[k])
                except ValueError:pass
        if d.get("state"):result.append(d)
    return result

def svg(title,w,h,body):return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"><rect width="100%" height="100%" fill="#f7f8fa"/><text x="28" y="34" font-family="sans-serif" font-size="22" font-weight="bold" fill="#243447">{title}</text>{body}</svg>'

def task_timeline(ecu_events,out):
    focus=ecu_events["ECU1"]
    ticks=[e.get("tick") for e in focus if isinstance(e.get("tick"),int)]
    if not ticks:return
    lo,hi=min(ticks),max(ticks); width=900; left=150; rows=["logger","heartbeat","sensor","normal","network","brake"]; colors={"logger":"#9aa4ad","heartbeat":"#8b79a8","sensor":"#62a87c","normal":"#e3a008","brake":"#d9534f","network":"#587ca5"}; spans={r:[] for r in rows}
    trace=[e for e in sorted(focus,key=lambda x:x.get("tick",0)) if e.get("state")=="SWITCH_IN" and e.get("task") in spans]
    if len(trace)>=2:
        for previous,current in zip(trace,trace[1:]):
            if current["tick"]>previous["tick"]: spans[previous["task"]].append((previous["tick"],current["tick"]))
    else:
        open_tasks={}
        for e in sorted(focus,key=lambda x:x.get("tick",0)):
            task=e.get("task"); state=e.get("state"); tick=e.get("tick")
            if task not in spans or not isinstance(tick,int):continue
            if state in ("START","EXECUTING"):open_tasks.setdefault(task,[]).append(tick)
            elif state=="DONE" and open_tasks.get(task):spans[task].append((open_tasks[task].pop(0),tick))
    body=""; scale=width/max(1,hi-lo)
    for i,task in enumerate(rows):
        y=70+i*45; body+=f'<text x="24" y="{y+20}" font-family="monospace" font-size="14">{task}</text><line x1="{left}" y1="{y+24}" x2="{left+width}" y2="{y+24}" stroke="#d5dbe1"/>'
        for a,b in spans[task]:
            x=left+(a-lo)*scale; w=max(3,(b-a)*scale); body+=f'<rect x="{x:.1f}" y="{y+8}" width="{w:.1f}" height="20" rx="4" fill="{colors[task]}"/>'
    body+=f'<text x="{left}" y="{70+len(rows)*45+25}" font-family="monospace">tick {lo}</text><text x="{left+width-60}" y="{70+len(rows)*45+25}" font-family="monospace">tick {hi}</text>'
    (out/"task_timeline_ecu1.svg").write_text(svg("ECU-1 FreeRTOS 任务时间轴",1120,70+len(rows)*45+55,body),encoding="utf-8")

def main():
    ap=argparse.ArgumentParser();ap.add_argument("run_dir",type=Path);a=ap.parse_args(); out=a.run_dir/"analysis";out.mkdir(exist_ok=True)
    ctrl=read_events(a.run_dir/"controller_serial.log"); ecu={f"ECU{i}":read_events(a.run_dir/f"ecu{i}_serial.log") for i in range(1,5)}
    sent=sum(e.get("state")=="SENT" for e in ctrl); received={n:sum(e.get("state")=="RECEIVED" for e in es) for n,es in ecu.items()}; pcap=a.run_dir/"traffic.pcap"; pcap_n=0
    if pcap.exists():pcap_n=sum(" IP " in x for x in subprocess.run(["tcpdump","-nn","-tt","-r",str(pcap),"udp port 5000"],capture_output=True,text=True).stdout.splitlines())
    delays=[]
    received_ticks={e.get("seq"):e.get("tick") for e in ecu["ECU1"] if e.get("state")=="RECEIVED" and isinstance(e.get("seq"),int) and isinstance(e.get("tick"),int)}
    for e in ecu["ECU1"]:
        if e.get("task")=="brake" and e.get("state")=="EXECUTING" and isinstance(e.get("seq"),int) and isinstance(e.get("tick"),int) and e.get("seq") in received_ticks:
            delays.append(e["tick"]-received_ticks[e["seq"]])
    summary={"sent":sent,"received_by_ecu":received,"pcap_udp_packets":pcap_n,"application_loss_by_ecu":{n:max(0,sent-v) for n,v in received.items()},"focus_ecu":"ECU1","focus_brake_receive_to_execute_ticks":delays,"average_focus_delay_ticks":sum(delays)/len(delays) if delays else None}
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    colors=["#587ca5","#5a9871","#9a7b4f","#8b79a8"];body=""
    for i,(n,v) in enumerate(received.items()):
        y=90+i*58;body+=f'<text x="70" y="{y+20}" font-family="sans-serif" font-size="16">{n}</text><rect x="160" y="{y}" width="{max(4,v*35)}" height="28" fill="{colors[i]}"/><text x="{175+max(4,v*35)}" y="{y+20}" font-family="monospace">{v}</text>'
    body+=f'<text x="70" y="370" font-family="sans-serif" font-size="17">控制器发送：{sent}　PCAP 捕获：{pcap_n}　重点 ECU 刹车平均延迟：{summary["average_focus_delay_ticks"]}</text>'
    (out/"multicast_statistics.svg").write_text(svg("四个平级 ECU 的 UDP 组播接收统计",900,450,body),encoding="utf-8")
    body='<line x1="220" y1="240" x2="470" y2="240" stroke="#416b96" stroke-width="4"/>'
    for i,y in enumerate((100,180,300,380),1):body+=f'<line x1="470" y1="240" x2="740" y2="{y}" stroke="#416b96" stroke-width="4"/><rect x="95" y="206" width="150" height="68" rx="12" fill="#587ca5"/><text x="170" y="240" text-anchor="middle" fill="white" font-family="sans-serif">控制器</text><rect x="400" y="206" width="170" height="68" rx="12" fill="#65727e"/><text x="485" y="240" text-anchor="middle" fill="white" font-family="sans-serif">brakebr0</text><rect x="700" y="{y-30}" width="150" height="60" rx="12" fill="#5a9871"/><text x="775" y="{y+5}" text-anchor="middle" fill="white" font-family="sans-serif">ECU-{i}</text>'
    body+='<text x="350" y="70" font-family="monospace" font-size="16">UDP 239.255.0.1:5000</text>'
    (out/"multicast_topology.svg").write_text(svg("控制器到四个平级 ECU 的 UDP 组播拓扑",950,500,body),encoding="utf-8")
    lines=['# 第二版实验分析','','- 控制器发送：'+str(sent),'']
    for n,v in received.items():lines.append(f'- {n} 接收：{v}')
    lines += [f'- PCAP 捕获 UDP 报文：{pcap_n}',f'- 重点 ECU-1 刹车接收→执行延迟：{delays}', '', '可视化：`multicast_topology.svg`、`multicast_statistics.svg`；重点 ECU-1 的串口日志用于生成任务泳道图。']
    task_timeline(ecu,out)
    (out/"analysis.md").write_text("\n".join(lines),encoding="utf-8");print(json.dumps(summary,indent=2))
if __name__=="__main__":main()
