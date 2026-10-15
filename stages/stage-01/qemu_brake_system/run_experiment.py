#!/usr/bin/env python3
"""Run one clean four-ECU experiment and preserve raw evidence."""
from __future__ import annotations
import argparse, json, os, selectors, socket, subprocess, time
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def start_qemu(kernel,tap,mac,serial,log):
    cmd=["qemu-system-arm","-machine","mps2-an385","-cpu","cortex-m3","-kernel",str(kernel),"-netdev",f"tap,id=n{serial},ifname={tap},script=no,downscript=no","-net",f"nic,macaddr={mac},model=lan9118,netdev=n{serial}","-monitor","none","-nographic","-serial",f"tcp:127.0.0.1:{serial},server=on,wait=on"]
    fp=log.open("w",encoding="utf-8")
    return subprocess.Popen(cmd,cwd=ROOT,stdout=fp,stderr=subprocess.STDOUT)

def connect(port,deadline):
    while time.monotonic()<deadline:
        try:
            s=socket.create_connection(("127.0.0.1",port),timeout=1); s.setblocking(False); return s
        except OSError: time.sleep(.1)
    raise RuntimeError(f"serial port {port} unavailable")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--duration",type=float,default=12); ap.add_argument("--name",default="run_01"); a=ap.parse_args()
    if os.geteuid()!=0: raise SystemExit("请使用 sudo python3 run_experiment.py")
    out=ROOT/"runtime"/a.name; out.mkdir(parents=True,exist_ok=True)
    for p in out.iterdir():
        if p.is_file(): p.unlink()
        elif p.is_dir():
            import shutil; shutil.rmtree(p)
    kernels=[ROOT/"build"/"controller_0"/"brake_controller_0.axf"]+[ROOT/"build"/f"ecu_{i}"/f"brake_ecu_{i}.axf" for i in range(1,5)]
    if not all(p.exists() for p in kernels): raise SystemExit("请先编译 controller_1 和 ecu_1..ecu_4")
    taps=["brakectrl0"]+[f"brakeecu{i}" for i in range(1,5)]
    macs=["52:54:00:12:34:A0"]+[f"52:54:00:12:34:A{i:X}" for i in range(1,5)]
    serials=[5101,5102,5103,5104,5105]
    capture=subprocess.Popen(["tcpdump","-i","brakebr0","-U","-w",str(out/"traffic.pcap"),"udp port 5000"],stdout=(out/"tcpdump.log").open("w"),stderr=subprocess.STDOUT)
    processes=[]
    for k,t,m,s in zip(kernels,taps,macs,serials): processes.append(start_qemu(k,t,m,s,out/("controller_qemu.log" if s==5101 else f"ecu{s-5101}_qemu.log")))
    sockets={}; files={}; sel=selectors.DefaultSelector(); buffers={}; start=time.monotonic()
    for name,port in [("CTRL",5101)]+[(f"ECU{i}",5101+i) for i in range(1,5)]:
        s=connect(port,time.monotonic()+12); sockets[name]=s; buffers[name]=""; sel.register(s,selectors.EVENT_READ,name)
        files[name]=(out/("controller_serial.log" if name=="CTRL" else f"ecu{name[3:]}_serial.log")).open("w",encoding="utf-8")
    commands=[(4.0,"NORMAL"),(5.0,"TURN"),(6.0,"NORMAL"),(7.0,"BRAKE"),(8.0,"NORMAL"),(9.0,"BRAKE"),(10.0,"TURN"),(11.0,"NORMAL"),(12.0,"BRAKE"),(13.0,"NORMAL"),(14.0,"BRAKE"),(15.0,"TURN"),(16.0,"BRAKE")]; index=0
    try:
        while time.monotonic()-start<a.duration:
            elapsed=time.monotonic()-start
            while index<len(commands) and elapsed>=commands[index][0]: sockets["CTRL"].sendall((commands[index][1]+"\n").encode()); index+=1
            for key,_ in sel.select(.05):
                data=key.fileobj.recv(8192)
                if not data: continue
                name=key.data; buffers[name]+=data.decode(errors="replace"); lines=buffers[name].split("\n"); buffers[name]=lines.pop()
                for line in lines:
                    if line.strip(): files[name].write(line.rstrip("\r")+"\n"); files[name].flush()
    finally:
        for s in sockets.values(): s.close()
        for f in files.values(): f.close()
        for p in processes:
            p.terminate()
        for p in processes:
            try: p.wait(3)
            except subprocess.TimeoutExpired: p.kill()
        capture.terminate()
        try: capture.wait(3)
        except subprocess.TimeoutExpired: capture.kill()
    metadata={"name":a.name,"ecu_count":4,"controller_ip":"10.10.0.10","ecu_ips":[f"10.10.0.{10+i}" for i in range(1,5)],"multicast":"239.255.0.1:5000","scheduled_commands":len(commands),"focus_ecu":"ECU-1","pcap":"traffic.pcap"}
    (out/"metadata.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8"); print(json.dumps(metadata,indent=2))
if __name__=="__main__": main()
