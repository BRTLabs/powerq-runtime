from __future__ import annotations
import csv, sqlite3
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile
COLUMNS=('timestamp','area_id','area_name','plc_id','plc_name','plc_ip','signal_id','signal_name','address','data_type','value','unit','quality')

def rows_to_sqlite(rows,path):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as c:
        c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=NORMAL')
        c.execute('CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY,timestamp TEXT NOT NULL,area_id TEXT,area_name TEXT,plc_id TEXT,plc_name TEXT,plc_ip TEXT,signal_id TEXT,signal_name TEXT,address TEXT,data_type TEXT,value TEXT,unit TEXT,quality TEXT)')
        c.execute('CREATE INDEX IF NOT EXISTS ix_readings_lookup ON readings(area_id,plc_id,signal_id,timestamp)')
        c.executemany('INSERT INTO readings('+','.join(COLUMNS)+') VALUES ('+','.join('?'*len(COLUMNS))+')',[tuple(str(r.get(k,'')) for k in COLUMNS) for r in rows])

def rows_to_csv(rows,path):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); new=not path.exists() or path.stat().st_size==0
    with path.open('a',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,COLUMNS,extrasaction='ignore')
        if new:w.writeheader()
        w.writerows(rows)

def _col(n):
    s=''
    while n:n,r=divmod(n-1,26);s=chr(65+r)+s
    return s

def rows_to_xlsx(rows,path):
    """Create a standards-compliant XLSX snapshot with only zipfile/XML."""
    vals=[COLUMNS]+[tuple(r.get(k,'') for k in COLUMNS) for r in rows]
    xml=['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for ri,row in enumerate(vals,1):
        xml.append(f'<row r="{ri}">')
        for ci,v in enumerate(row,1):
            ref=f'{_col(ci)}{ri}'
            if isinstance(v,(int,float)) and not isinstance(v,bool): xml.append(f'<c r="{ref}"><v>{v}</v></c>')
            else: xml.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>')
        xml.append('</row>')
    xml.append('</sheetData></worksheet>')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp.xlsx')
    with ZipFile(tmp,'w',ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml','<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Readings" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',''.join(xml))
    tmp.replace(path)