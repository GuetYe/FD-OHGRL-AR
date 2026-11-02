"""V7 wired realization of the selected Dreamer graph; no wireless semantics imported."""
import json,time
from pathlib import Path
from mininet.net import Mininet
from mininet.link import TCLink
from mininet.node import RemoteController,OVSSwitch
from mininet.cli import CLI
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=json.loads((ROOT/'v7_topology.json').read_text());physical=json.loads((ROOT/'network_profile.json').read_text())
    links=json.loads((ROOT/'v7_ports.json').read_text())
    opts=dict(delay=str(physical['initial_delay_ms'])+'ms',loss=physical['initial_loss_percent'],max_queue_size=physical['queue_limit_packets'])
    net=Mininet(controller=None,link=TCLink,switch=OVSSwitch,build=False,waitConnected=True)
    net.addController('c0',controller=RemoteController,ip='127.0.0.1',port=6633)
    switches={n:net.addSwitch('s%d'%n,dpid='%016x'%n,protocols='OpenFlow13') for n in p['nodes']}
    for e in links:net.addLink(switches[e['u']],switches[e['v']],port1=e['port1'],port2=e['port2'],bw=physical['switch_bw_mbps'],**opts)
    for n in p['hosts']:
        host=net.addHost('h%d'%n,ip='10.0.0.%d/24'%n,mac='02:00:00:00:00:%02x'%n)
        # Degree >8 is possible in larger graphs; reserve host port100 there.
        port=9 if p['name']=='original18' else 100
        if any((e['u']==n and e['port1']==port) or (e['v']==n and e['port2']==port) for e in links):raise ValueError('Host/inter-switch port collision')
        net.addLink(host,switches[n],port1=0,port2=port,bw=physical['access_bw_mbps'],**opts)
    try:
        net.build();net.start()
        # V6 INIT_TIME=30 and periodic LLDP discovery remain unchanged.
        time.sleep(35)
        for host in net.hosts:host.cmd('ip route replace default dev '+host.defaultIntf().name)
        loss=net.pingAll()
        if loss:raise RuntimeError('Startup pingAll failed: %s percent'%loss)
        print('V7_TOPOLOGY_READY '+p['name'],flush=True)
        CLI(net)
    finally:net.stop()
if __name__=='__main__':main()
