import base64, sys
from copy import deepcopy
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, Field
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from report_pdf import build_report
app=FastAPI(title='Terra reports')
@app.middleware('http')
async def size_limit(request: Request, call_next):
    try: size=int(request.headers.get('content-length','0'))
    except ValueError: return JSONResponse({'detail':'Invalid content length'},status_code=400)
    if size>4000000:return JSONResponse({'detail':'Report exceeds the maximum size'},status_code=413)
    return await call_next(request)
class PortableReportRequest(BaseModel):
    result: dict
    categories: list[str] | None = Field(default=None,max_length=10)
    language: str = 'en'
@app.post('/api/index.py')
@app.post('/api/v1/reports/generate')
def report(body: PortableReportRequest):
    result=deepcopy(body.result)
    if not isinstance(result.get('bbox'),list) or len(result['bbox'])!=4 or 'area_ha' not in result: raise HTTPException(422,'Invalid report data')
    if 'rows' in result:
        if not isinstance(result['rows'],list) or len(result['rows'])>10: raise HTTPException(422,'Invalid report rows')
        if body.categories is not None:
            selected=set(body.categories);result['rows']=[r for r in result['rows'] if r.get('class') in selected]
    elif 'scenes' not in result: raise HTTPException(422,'Unsupported report type')
    image_store={}
    if 'scenes' in result:
        for name,uri in result.get('images_data',{}).items():
            if name not in ('before','after','loss') or not isinstance(uri,str) or not uri.startswith('data:image/png;base64,') or len(uri)>1800000: raise HTTPException(422,'Invalid report image')
            try: raw=base64.b64decode(uri.split(',',1)[1],validate=True)
            except Exception: raise HTTPException(422,'Invalid image encoding')
            if not raw.startswith(b'\x89PNG\r\n\x1a\n'): raise HTTPException(422,'Invalid PNG image')
            image_store[name]=raw
        result['images']={name:'/api/v1/image/'+name+'.png' for name in image_store}
    try: pdf=build_report(result,image_store,body.language if body.language in ('en','ru') else 'en')
    except (KeyError,TypeError,ValueError): raise HTTPException(422,'Incomplete or invalid report data')
    return Response(pdf,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="terra-report.pdf"'})
