from collections.abc import Iterable

def normalize_area_transitions(payload: Iterable[object]) -> list[tuple[str,int]]:
    latest={}
    for item in payload:
        if not isinstance(item,(tuple,list)) or len(item)!=2: raise ValueError('invalid area transition')
        area=str(item[0]).strip(); raw=item[1]
        if not area or isinstance(raw,bool): raise ValueError('invalid area transition')
        try:
            state=int(raw)
        except (TypeError, ValueError) as error:
            raise ValueError('state must be 0, 1, or 2') from error
        if state not in (0,1,2): raise ValueError('state must be 0, 1, or 2')
        latest[area]=state
    return list(latest.items())