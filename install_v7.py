"""Prepare topology-specific private copies of actual B-v6 sources. Python3.6+."""
import argparse,ast,hashlib,json,shutil,sys,xml.etree.ElementTree as ET
from pathlib import Path
from common import HERE,read,write,sha,profile,make_config,ports,normalize_loopback

def replace_once(s,old,new):
    if s.count(old)!=1:raise ValueError('V6 source contract failed: '+old[:100])
    return s.replace(old,new)

def link_xml(p,physical,target):
    root=ET.Element('links_info')
    for edge in ports(p):
        item=ET.SubElement(root,'links');item.text=str((edge['u'],edge['v']))
        fields=dict(ports=str((edge['port1'],edge['port2'])),free_bw=physical['switch_bw_mbps'],
            delay=physical['initial_delay_ms'],loss=physical['initial_loss_percent'],used_bw=0,pkt_err=0,pkt_drop=0,distance=1)
        for k,v in fields.items():ET.SubElement(item,k).text=str(v)
    target.parent.mkdir(parents=True,exist_ok=True)
    ET.ElementTree(root).write(str(target),encoding='utf-8',xml_declaration=True)

def prepare(name,root,upper_python,lower_python):
    cell=root/name;p=profile(name)
    if (cell/'installed.json').exists():
        record=read(cell/'installed.json')
        if record['package_sha256']!=sha(HERE/'SHA256.json'):raise RuntimeError('Prepared code differs; use a new --runtime-root')
        for f,digest in record['source_files'].items():
            if not (cell/f).is_file() or sha(cell/f)!=digest:raise RuntimeError('Prepared source changed: '+f)
        print('[READY]',cell);return
    if cell.exists():raise RuntimeError('Partial directory retained; use a new runtime root: '+str(cell))
    cell.mkdir(parents=True)
    for part in ['upper','lower']:shutil.copytree(str(HERE/'vendor'/part),str(cell/part))
    lower=cell/'lower';upper=cell/'upper';s3=cell/'s3';s3.mkdir()
    write(cell/'topology.json',p);cfg=make_config(name);write(cell/'config.json',cfg)
    physical=cfg['normalization_profile']
    write(lower/'network_profile.json',physical);write(upper/'core/network_profile.json',physical)
    # Private lower settings only. Lease lifecycle and per-flow controller code are retained.
    for target in [lower/'setting.py',lower/'ryu/setting.py']:
        s=target.read_text(encoding='utf-8')
        s+='\n# V7 private topology endpoints and loopback API.\n'
        s+='src_node_list='+repr([p['source']])+'\ndst_node_list='+repr(p['destinations'])+'\n'
        s+="data_server_ip='127.0.0.1'\nserver_ip='127.0.0.1'\n"
        if target.parent==lower:
            s+="server_ip_port={str((src,dst)):('10.0.0.%d'%dst,5001) for src in src_node_list for dst in dst_node_list}\n"
            s+="choice_pro={key:1.0/len(server_ip_port) for key in server_ip_port}\n"
        else:
            s+="src_node_ip_list=['10.0.0.%d'%n for n in src_node_list]\ndst_node_ip_list=['10.0.0.%d'%n for n in dst_node_list]\n"
        target.write_text(normalize_loopback(s),encoding='utf-8')
    # Retain the V6 access-only ARP guard and exact physical XML/port checks;
    # replace its wired18 assertion with this topology's exact declared graph.
    target=lower/'ryu/network_structure.py';s=target.read_text(encoding='utf-8')
    old='if nodes != set(range(1, 19)) or len(edges) != 24 or edges != expected:'
    new="if nodes != set("+repr(p['nodes'])+") or edges != {tuple(sorted(e)) for e in "+repr(p['edges'])+"} or edges != expected:"
    s=replace_once(s,old,new)
    s=s.replace('ARP guard requires current wired-18 / 24-link XML; refusing unsafe flooding','V7 ARP guard: physical XML differs from selected topology')
    s=s.replace('ARP_ACCESS_ONLY active: switches=18 links=24','ARP_ACCESS_ONLY V7 '+name+': switches='+str(len(p['nodes']))+' links='+str(len(p['edges'])))
    target.write_text(s,encoding='utf-8')
    shutil.copy2(str(HERE/'topology_runtime.py'),str(lower/'mininet_wifi/generate_wired_topo18.py'))
    # Config is found relative to the private lower root by the topology launcher.
    write(lower/'v7_topology.json',p);write(lower/'v7_ports.json',ports(p))
    link_xml(p,physical,lower/'mininet_wifi/save_links_info/links_info.xml')
    s=(HERE/'vendor/s3.py').read_text(encoding='utf-8')
    s=replace_once(s,"PYLOW = '/opt/ohgrl-lower/venv/bin/python'",'PYLOW = '+repr(str(lower_python)))
    s=replace_once(s,'if host not in [15,16,17,18]:',"if host not in "+repr(p['destinations'])+':')
    (s3/'s3.py').write_text(s,encoding='utf-8')
    (s3/'s3.sh').write_text('#!/bin/bash\nexec '+str(lower_python)+' -u '+str(s3/'s3.py')+' "$@"\n',encoding='utf-8')
    write(s3/'paths.json',dict(lower=str(lower),upper=str(upper),upper_python=str(upper_python),lower_python=str(lower_python)))
    # Original18 retains exactly the historical 128-state diagnostic bank.
    if name=='original18':
        shutil.copy2(str(HERE/'banks/original18.json'),str(cell/'fixed_bank.json'))
    tracked={str(f.relative_to(cell)):sha(f) for part in [upper,lower,s3] for f in part.rglob('*')
             if f.is_file() and f.suffix in {'.py','.sh','.json','.xml'}}
    write(cell/'installed.json',dict(topology=name,package_sha256=sha(HERE/'SHA256.json'),source_files=tracked,
        endpoints=dict(source=p['source'],destinations=p['destinations']),source='actual exported B-v6; private V7 modifications'))
    print('[PREPARED]',cell)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--topology',default='all',choices=['all']+list(read(HERE/'topologies.json')))
    parser.add_argument('--runtime-root',type=Path,default=Path('/home/wp/ohgrl_lab/OHGRL_v7/runtime'))
    parser.add_argument('--upper-python',default='/home/wp/anaconda3/envs/Evans0/bin/python3.9')
    parser.add_argument('--lower-python',default='/opt/ohgrl-lower/venv/bin/python')
    a=parser.parse_args()
    for rel,digest in read(HERE/'SHA256.json').items():
        if sha(HERE/rel)!=digest:raise ValueError('Bundle changed: '+rel)
    if ' ' in str(a.runtime_root) or not a.runtime_root.is_absolute():raise ValueError('Use an absolute runtime root without spaces')
    for name in (read(HERE/'topologies.json') if a.topology=='all' else [a.topology]):prepare(name,a.runtime_root,a.upper_python,a.lower_python)
if __name__=='__main__':main()
