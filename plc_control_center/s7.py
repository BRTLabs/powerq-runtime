from __future__ import annotations
import socket, struct
SIZES={'BOOL':1,'BYTE':1,'WORD':2,'INT':2,'DINT':4,'DWORD':4,'REAL':4}

def decode(data: bytes, typ: str, bit=0):
    typ=typ.upper()
    if len(data)!=SIZES[typ]: raise ValueError('Wrong PLC payload size')
    if typ=='BOOL': return bool(data[0] & (1<<bit))
    if typ=='BYTE': return data[0]
    if typ in ('WORD','DWORD'): return int.from_bytes(data,'big')
    if typ in ('INT','DINT'): return int.from_bytes(data,'big',signed=True)
    if typ=='REAL': return struct.unpack('>f',data)[0]
    raise ValueError(typ)

class S7Client:
    """Small read-only ISO-on-TCP/S7 client. One instance per worker thread."""
    def __init__(self, host, rack=0, slot=1, timeout=3.0): self.host=host; self.rack=rack; self.slot=slot; self.timeout=timeout; self.sock=None; self.pdu_ref=1
    def _recv_exact(self,n):
        b=b''
        while len(b)<n:
            x=self.sock.recv(n-len(b))
            if not x: raise ConnectionError('PLC closed the connection')
            b+=x
        return b
    def _exchange(self,payload):
        self.sock.sendall(b'\x03\x00'+struct.pack('>H',len(payload)+4)+payload)
        h=self._recv_exact(4)
        if h[:2]!=b'\x03\x00': raise ConnectionError('Invalid TPKT response')
        return self._recv_exact(struct.unpack('>H',h[2:])[0]-4)
    def connect(self):
        self.close(); self.sock=socket.create_connection((self.host,102),self.timeout); self.sock.settimeout(self.timeout)
        tsap=0x0100 + self.rack*0x20 + self.slot
        cotp=b'\x11\xe0\x00\x00\x00\x01\x00\xc1\x02\x01\x00\xc2\x02'+struct.pack('>H',tsap)+b'\xc0\x01\x0a'
        r=self._exchange(cotp)
        if len(r)<2 or r[1]!=0xd0: raise ConnectionError('COTP connection rejected')
        setup=b'\x02\xf0\x80'+b'\x32\x01\x00\x00\x00\x01\x00\x08\x00\x00'+b'\xf0\x00\x00\x01\x00\x01\x03\xc0'
        r=self._exchange(setup)
        if b'\xf0' not in r: raise ConnectionError('S7 setup rejected')
    def db_read(self, db, start, size):
        ref=self.pdu_ref; self.pdu_ref=(ref%65535)+1
        param=b'\x04\x01\x12\x0a\x10\x02'+struct.pack('>H',size)+struct.pack('>H',db)+b'\x84'+(start*8).to_bytes(3,'big')
        s7=b'\x32\x01\x00\x00'+struct.pack('>H',ref)+struct.pack('>H',len(param))+b'\x00\x00'+param
        r=self._exchange(b'\x02\xf0\x80'+s7)
        i=r.find(b'\x32\x03')
        if i<0 or len(r)<i+14: raise ConnectionError('Invalid S7 read response')
        plen=int.from_bytes(r[i+6:i+8],'big'); dlen=int.from_bytes(r[i+8:i+10],'big'); data=r[i+12+plen:i+12+plen+dlen]
        if len(data)<4 or data[0]!=0xff: raise RuntimeError(f'PLC read failed, code {data[0] if data else "missing"}')
        raw=data[4:4+size]
        if len(raw)!=size: raise RuntimeError('Short PLC read')
        return raw
    def close(self):
        if self.sock:
            try:self.sock.close()
            finally:self.sock=None