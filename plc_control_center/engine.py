from __future__ import annotations
import queue, threading, time
from datetime import datetime, timezone
from pathlib import Path
from .s7 import S7Client, SIZES, decode
from .storage import rows_to_sqlite
from .interpreter import load_config, evaluate_areas, persist_area_states

class Engine:
    def __init__(self,events): self.events=events; self.stop_event=threading.Event(); self.threads=[]; self.writer=None; self.records=queue.Queue(maxsize=1000); self.snapshot=[]; self.lock=threading.Lock(); self.raw_states={}; self.area_state_cache={}; self.database_path=None
    @property
    def running(self): return bool(self.writer and self.writer.is_alive())
    def start(self,p):
        if self.running: raise RuntimeError('Engine already running')
        self.stop_event.clear();self.threads=[];self.snapshot=[]
        path=Path(p.output_folder)/(p.dataset+'.db'); self.database_path=path
        self.writer=threading.Thread(target=self._write,args=(path,),daemon=True,name='PLCWriter');self.writer.start()
        for a in p.areas:
            for plc in a.plcs:
                if plc.enabled:
                    t=threading.Thread(target=self._poll,args=(a,plc),daemon=True,name=f'PLC-{plc.name}');self.threads.append(t);t.start()
        self.events.put(('engine',f'Running {len(self.threads)} PLC workers'))
    def stop(self): self.stop_event.set()
    def wait(self, timeout=3.0):
        """Wait briefly for pollers and the writer to flush queued records."""
        deadline=time.monotonic()+timeout
        for thread in [*self.threads,self.writer]:
            if thread is None:continue
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            thread.join(remaining)
    def _poll(self,a,p):
        backoff=1.0
        while not self.stop_event.is_set():
            c=S7Client(p.ip,p.rack,p.slot)
            try:
                c.connect();backoff=1;self.events.put(('plc',(p.id,'Connected')))
                while not self.stop_event.is_set():
                    now=datetime.now(timezone.utc).isoformat();batch=[]
                    # Merge overlapping/adjacent reads per DB, reducing PLC round trips.
                    groups={}
                    for s in p.signals: groups.setdefault(s.db,[]).append(s)
                    for db,signals in groups.items():
                        ordered=sorted(signals,key=lambda s:s.byte);blocks=[]
                        for s in ordered:
                            end=s.byte+SIZES[s.type]
                            if not blocks or s.byte>blocks[-1][1]+4: blocks.append([s.byte,end,[s]])
                            else: blocks[-1][1]=max(blocks[-1][1],end);blocks[-1][2].append(s)
                        for start,end,members in blocks:
                            raw=c.db_read(db,start,end-start)
                            for s in members:
                                value=decode(raw[s.byte-start:s.byte-start+SIZES[s.type]],s.type,s.bit)
                                batch.append({'timestamp':now,'area_id':a.id,'area_name':a.name,'plc_id':p.id,'plc_name':p.name,'plc_ip':p.ip,'signal_id':s.id,'signal_name':s.name,'address':f'DB{s.db}.{s.byte}'+(f'.{s.bit}' if s.type=='BOOL' else ''),'data_type':s.type,'value':value,'unit':s.unit,'quality':'GOOD'})
                    if batch:
                        self.records.put(batch,timeout=1)
                        with self.lock:
                            self.snapshot.extend(batch); self.snapshot=self.snapshot[-50000:]
                            # Cache each source independently so one PLC may provide
                            # any number of signals used by the Python area rules.
                            for row in batch:
                                self.raw_states[(p.name, row['signal_name'])] = row['value']
                            raw_snapshot = dict(self.raw_states)
                        try:
                            states=evaluate_areas(load_config(), raw_snapshot)
                            changes=persist_area_states(self.database_path, states, self.area_state_cache)
                            if changes:self.events.put(('areas',changes))
                        except Exception as exc:self.events.put(('interpreter_error',str(exc)))
                        self.events.put(('readings',batch))
                    self.stop_event.wait(p.poll)
            except Exception as e:
                self.events.put(('plc',(p.id,f'Error: {e}')));self.stop_event.wait(backoff);backoff=min(backoff*2,30)
            finally:c.close()
    def _write(self,path):
        pending=[];last=time.monotonic()
        try:
            while not self.stop_event.is_set() or not self.records.empty():
                try:pending.extend(self.records.get(timeout=.25))
                except queue.Empty:pass
                if pending and (len(pending)>=200 or time.monotonic()-last>=1): rows_to_sqlite(pending,path);pending.clear();last=time.monotonic();self.events.put(('stored',str(path)))
            if pending:rows_to_sqlite(pending,path)
        except Exception as e:self.events.put(('error',f'Storage failure: {e}'));self.stop_event.set()
        finally:self.events.put(('stopped',None))
    def snapshot_rows(self):
        with self.lock:return list(self.snapshot)