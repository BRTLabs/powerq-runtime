import re
STATE_NAMES={0:'Fault',1:'Idle',2:'Running'}
STATE_COLORS={0:'#D9342B',1:'#7A7A7A',2:'#00B56A'}
def canonical_area_id(name):
    clean=re.sub(r'[^A-Za-z0-9]+','_',str(name).strip()).strip('_')
    return clean if clean.casefold().startswith('area_') else f'Area_{clean}'
def raw_value_to_state(value):
    if isinstance(value,bool): return None
    try: value=int(value)
    except (TypeError,ValueError): return None
    return 0 if value==0 else 1 if 1<=value<=9 else 2 if 10<=value<=20 else None