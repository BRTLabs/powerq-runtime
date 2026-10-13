from __future__ import annotations
import ipaddress, socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from .s7 import S7Client

def local_network(prefix=24):
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:s.connect(('8.8.8.8',80)); ip=s.getsockname()[0]
    except OSError: ip=socket.gethostbyname(socket.gethostname())
    finally:s.close()
    return ipaddress.ip_network(f'{ip}/{prefix}',strict=False)

def _probe(ip,timeout):
    c=S7Client(str(ip),timeout=timeout)
    try:c.connect(); return {'ip':str(ip),'name':f'Siemens PLC {ip}','model':'S7 endpoint'}
    except Exception:return None
    finally:c.close()

def scan(network=None, timeout=.35, workers=64):
    net=ipaddress.ip_network(network,strict=False) if network else local_network()
    if net.num_addresses>1024: raise ValueError('Scan range is limited to 1024 addresses.')
    out=[]
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='PLCScan') as pool:
        futures=[pool.submit(_probe,ip,timeout) for ip in net.hosts()]
        for f in as_completed(futures):
            r=f.result()
            if r:out.append(r)
    return sorted(out,key=lambda x:ipaddress.ip_address(x['ip']))