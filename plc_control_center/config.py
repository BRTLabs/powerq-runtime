from __future__ import annotations
import ipaddress, json, re
from pathlib import Path
from .models import Area, Plc, Project, Signal
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG = BASE_DIR/'data'/'plc_project.json'
TYPES = {'BOOL':1,'BYTE':1,'WORD':2,'INT':2,'DINT':4,'DWORD':4,'REAL':4}

def validate(p: Project):
    if not p.areas: raise ValueError('Add at least one area.')
    seen=set()
    for a in p.areas:
        key=a.name.strip().casefold()
        if not key or key in seen: raise ValueError('Area names must be non-empty and unique.')
        seen.add(key)
        for plc in a.plcs:
            if plc.enabled: ipaddress.ip_address(plc.ip)
            if plc.poll < .25: raise ValueError(f'{plc.name}: polling must be at least 0.25 s.')
            for s in plc.signals:
                if not s.name.strip() or s.type not in TYPES or s.db < 1 or s.byte < 0 or not 0 <= s.bit <= 7: raise ValueError(f'{plc.name}: invalid signal.')
    if not re.fullmatch(r'[A-Za-z0-9._-]+',p.dataset): raise ValueError('Dataset may use letters, numbers, dot, dash and underscore.')

def save(p: Project, path=CONFIG):
    validate(p); path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(p.dict(),indent=2),encoding='utf-8'); tmp.replace(path)

def load(path=CONFIG):
    if not path.exists(): return Project(output_folder=str(BASE_DIR/'data'), dataset='operations')
    try:
        x=json.loads(path.read_text(encoding='utf-8'))
        areas=[]
        for a in x.get('areas',[]):
            plcs=[]
            for q in a.get('plcs',[]):
                signals=[Signal(**s) for s in q.pop('signals',[])]
                plcs.append(Plc(**q,signals=signals))
            areas.append(Area(**{k:v for k,v in a.items() if k!='plcs'},plcs=plcs))
        return Project(**{k:v for k,v in x.items() if k!='areas'},areas=areas)
    except Exception: return Project(output_folder=str(BASE_DIR/'data'), dataset='operations')