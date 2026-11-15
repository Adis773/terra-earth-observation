import io, json, logging, threading, uuid
from datetime import date
from functools import lru_cache
from pathlib import Path
import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.windows import from_bounds, Window, transform as window_transform
from rasterio.warp import transform_bounds
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import Response
import torch
from torch import nn
import torch.nn.functional as F
import landcover

app = FastAPI(title="Terra API", version="2.0.0", description="Satellite imagery and land-cover estimates. Scene availability and model quality vary by area.")
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['GET','POST','OPTIONS'], allow_headers=['*'])
classes = ["water", "trees", "flooded_vegetation", "crops", "built_up", "bare_ground", "snow_ice", "rangeland"]
labels = {"water":"Вода", "trees":"Деревья", "flooded_vegetation":"Затопленная растительность", "crops":"Посевы", "built_up":"Застройка", "bare_ground":"Открытая почва", "snow_ice":"Снег и лёд", "rangeland":"Травянистая растительность"}
exports = {}
reports = {}
model_lock = threading.Lock()

def block(i,o):
    return nn.Sequential(nn.Conv2d(i,o,3,padding=1,bias=False),nn.BatchNorm2d(o),nn.ReLU(inplace=True),nn.Conv2d(o,o,3,padding=1,bias=False),nn.BatchNorm2d(o),nn.ReLU(inplace=True))
@lru_cache(maxsize=1)
def forest_model():
    b=16
    net=nn.ModuleDict({"e1":block(5,b),"e2":block(b,b*2),"e3":block(b*2,b*4),"e4":block(b*4,b*8),"mid":nn.Sequential(block(b*8,b*16),nn.Dropout2d(.2)),"u4":nn.ConvTranspose2d(b*16,b*8,2,stride=2),"d4":block(b*16,b*8),"u3":nn.ConvTranspose2d(b*8,b*4,2,stride=2),"d3":block(b*8,b*4),"u2":nn.ConvTranspose2d(b*4,b*2,2,stride=2),"d2":block(b*4,b*2),"u1":nn.ConvTranspose2d(b*2,b,2,stride=2),"d1":block(b*2,b),"out":nn.Conv2d(b,1,1)})
    ckpt=torch.load(Path(__file__).parent/'forest_unet.pt',map_location='cpu',weights_only=True)
    net.load_state_dict(ckpt['state_dict']); net.eval(); return net

def forest_predict(a):
    net=forest_model(); h,w=a.shape[1:]; result=np.zeros((h,w),np.float32)
    for r in range(0,h,256):
      for c in range(0,w,256):
        x=a[:,r:r+256,c:c+256].astype(np.float32); ph,pw=x.shape[1:]
        red,nir=x[0],x[3]; nd=(nir-red)/(nir+red+1e-6)
        x=np.concatenate([np.clip(x/4000,0,1),nd[None]],axis=0);x=np.nan_to_num(x)
        t=torch.from_numpy(x[None]);t=F.pad(t,(0,(-pw)%16,0,(-ph)%16),mode='replicate')
        with model_lock,torch.inference_mode():
          e1=net['e1'](t);e2=net['e2'](F.max_pool2d(e1,2));e3=net['e3'](F.max_pool2d(e2,2));e4=net['e4'](F.max_pool2d(e3,2));m=net['mid'](F.max_pool2d(e4,2));d=net['d4'](torch.cat([net['u4'](m),e4],1));d=net['d3'](torch.cat([net['u3'](d),e3],1));d=net['d2'](torch.cat([net['u2'](d),e2],1));d=net['d1'](torch.cat([net['u1'](d),e1],1));result[r:r+ph,c:c+pw]=torch.sigmoid(net['out'](d))[0,0,:ph,:pw].numpy()
    return result

def read_image(f, count):
    data=f.file.read(200*1024*1024+1)
    if len(data)>200*1024*1024: raise HTTPException(413,'Файл больше 200 МБ')
    try:
      mem=MemoryFile(data);ds=mem.open()
      if ds.count!=count: raise ValueError(f'Ожидается {count} каналов, получено {ds.count}')
      if not ds.crs or not ds.crs.is_projected or ds.crs.linear_units.lower() not in ('metre','meter','metres','meters','m'): raise ValueError('Требуется проекционная CRS в метрах')
      if ds.width*ds.height>4_000_000: raise ValueError('Максимум 4 млн пикселей')
      if abs(ds.transform.b)>1e-10 or abs(ds.transform.d)>1e-10 or ds.transform.a<=0 or ds.transform.e>=0: raise ValueError('Требуется сетка без поворота, север сверху')
      return mem,ds
    except Exception as e:
      if 'ds' in locals(): ds.close()
      if 'mem' in locals(): mem.close()
      raise HTTPException(422,f'Ошибка GeoTIFF: {e}')

@app.get('/api/health')
def health(): return {'status':'ok'}

@app.post('/api/analyze')
def analyze(before: UploadFile=File(...), after: UploadFile=File(None), mode: str=Form(...), bbox: str=Form(...), before_date: str=Form(...), after_date: str=Form(''), before_offset: int=Form(0), after_offset: int=Form(0)):
    if mode not in ('cover','forest'): raise HTTPException(422,'Неизвестный режим')
    try:
      box=json.loads(bbox);assert len(box)==4 and all(isinstance(n,(int,float)) and np.isfinite(n) for n in box)
      west,south,east,north=box;assert -180<=west<east<=180 and -85<=south<north<=85
      date.fromisoformat(before_date)
      if after: assert after_date and date.fromisoformat(after_date)>=date.fromisoformat(before_date)
      assert before_offset in (0,1000) and after_offset in (0,1000)
    except (ValueError,AssertionError,TypeError): raise HTTPException(422,'Проверьте область, даты и смещение')
    mem1,d1=read_image(before,13 if mode=='cover' else 4);mem2=d2=None
    try:
      if after:
        mem2,d2=read_image(after,13 if mode=='cover' else 4)
        if d1.crs!=d2.crs or d1.transform!=d2.transform or d1.shape!=d2.shape: raise HTTPException(422,'Снимки должны иметь одинаковую CRS, размеры и сетку')
      bounds=transform_bounds('EPSG:4326',d1.crs,*box,densify_pts=21)
      dsbounds=d1.bounds
      if bounds[0]<dsbounds.left or bounds[1]<dsbounds.bottom or bounds[2]>dsbounds.right or bounds[3]>dsbounds.top: raise HTTPException(422,'Выделенная область должна полностью находиться внутри снимка')
      raw=from_bounds(*bounds,transform=d1.transform);left=int(np.floor(raw.col_off));top=int(np.floor(raw.row_off));right=int(np.ceil(raw.col_off+raw.width));bottom=int(np.ceil(raw.row_off+raw.height));win=Window(left,top,right-left,bottom-top)
      # Mask the actual WGS84 rectangle rather than its bounding box in the projected CRS.
      from rasterio.features import geometry_mask
      from rasterio.warp import transform_geom
      geom={'type':'Polygon','coordinates':[[[west,south],[east,south],[east,north],[west,north],[west,south]]]}
      projected=transform_geom('EPSG:4326',d1.crs,geom)
      inside=geometry_mask([projected],out_shape=(int(win.height),int(win.width)),transform=window_transform(win,d1.transform),invert=True)
      a=d1.read(window=win,masked=True);b=d2.read(window=win,masked=True) if d2 else None
      valid=inside & ~np.any(np.ma.getmaskarray(a),axis=0) & np.all(np.isfinite(a.data),axis=0)
      if b is not None: valid &= ~np.any(np.ma.getmaskarray(b),axis=0) & np.all(np.isfinite(b.data),axis=0)
      if not np.any(valid): raise HTTPException(422,'Нет корректных пикселей в выбранной области')
      if mode=='cover':
        p1=landcover.predict(a.filled(0),before_offset);p2=landcover.predict(b.filled(0),after_offset) if b is not None else None
        valid &= p1!=7
        if p2 is not None:valid &= p2!=7
        # Blue Marble class 8 is rangeland, with cloud excluded.
        ids=[0,1,2,3,4,5,6,8]
      else:
        p1=forest_predict(a.filled(0));p2=forest_predict(b.filled(0)) if b is not None else None
        ids=[1]
      n=int(valid.sum())
      if not n: raise HTTPException(422,'Нет общих безоблачных пикселей')
      area=abs(d1.transform.a*d1.transform.e-d1.transform.b*d1.transform.d)/10000;rows=[]
      for index,name in zip(ids,classes if mode=='cover' else ['trees']):
        q1=(p1==index) if mode=='cover' else (p1>.5)
        q2=((p2==index) if mode=='cover' else (p2>.5)) if p2 is not None else None
        c1=int((q1&valid).sum());c2=int((q2&valid).sum()) if q2 is not None else None
        rows.append({'class':name,'label':labels[name],'before_ha':round(c1*area,3),'before_percent':round(c1/n*100,2),'after_ha':round(c2*area,3) if c2 is not None else None,'after_percent':round(c2/n*100,2) if c2 is not None else None,'change_ha':round((c2-c1)*area,3) if c2 is not None else None,'relative_change_percent':round((c2-c1)/c1*100,2) if c2 is not None and c1 else None})
      result={'mode':mode,'dates':[before_date,after_date] if after else [before_date],'bbox':box,'area_ha':round(n*area,3),'coverage_percent':round(n/max(1,int(inside.sum()))*100,2),'rows':rows,'warning':('U-Net обучена в Японии; точность в других регионах не проверена. Требуется маска облаков в исходных данных.' if mode=='forest' else 'Площадь приблизительная. Деревья не обязательно являются лесом; облака определяются моделью.')}
      if mode=='forest' and p2 is not None:
        loss=valid & (p1>.5) & (p2<.35);result['forest_loss_ha']=round(int(loss.sum())*area,3)
        out=np.where(valid,loss.astype('uint8'),255).astype('uint8')
      else: out=np.where(valid,p2 if p2 is not None else p1,255).astype('uint8') if mode=='cover' else np.where(valid,(p1>.5).astype('uint8'),255).astype('uint8')
      profile={'driver':'GTiff','height':out.shape[0],'width':out.shape[1],'count':1,'dtype':'uint8','crs':d1.crs,'transform':window_transform(win,d1.transform),'nodata':255,'compress':'deflate'}
      with MemoryFile() as m:
        with m.open(**profile) as dst: dst.write(out,1)
        tif=m.read()
      key=str(uuid.uuid4());exports[key]=tif
      if len(exports)>20: exports.pop(next(iter(exports)))
      result['geotiff_url']='/api/export/'+key+'.tif';return result
    except HTTPException: raise
    except Exception as e:
      logging.exception('Analysis failed');raise HTTPException(500, 'Не удалось обработать снимки. Подробности в журнале сервера.') from e
    finally:
      d1.close();mem1.close()
      if d2:d2.close()
      if mem2:mem2.close()

@app.get('/api/export/{name}')
def export(name:str):
    key=name.removesuffix('.tif')
    if key not in exports: raise HTTPException(404,'Файл не найден или срок хранения истёк')
    return Response(exports[key],media_type='image/tiff',headers={'Content-Disposition':'attachment; filename="analysis.tif"'})


# Automated, global Sentinel-2-derived annual land cover (Impact Observatory / Esri).
import requests
import pystac_client
from pydantic import BaseModel, Field
from pyproj import Geod
from rasterio.transform import from_bounds as raster_from_bounds

LAND_CATEGORIES = {
 "trees": (2, "Деревья"), "water": (1, "Вода"), "flooded_vegetation": (4, "Затопленная растительность"),
 "crops": (5, "Посевы"), "built_up": (7, "Застройка"), "bare_ground": (8, "Открытая почва"),
 "snow_ice": (9, "Снег и лёд"), "rangeland": (11, "Травянистая растительность"),
 "forest_loss": (None, "Потеря деревьев"), "forest_gain": (None, "Появление деревьев")}
COLLECTION = 'io-lulc-annual-v02'
STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1'
TILER = 'https://planetarycomputer.microsoft.com/api/data/v1/item/bbox'

class AreaRequest(BaseModel):
    bbox: list[float] = Field(min_length=4, max_length=4, description='WGS84: west, south, east, north')
    year: int = Field(ge=2017, le=2023)
    compare_year: int | None = Field(default=None, ge=2017, le=2023)
    categories: list[str] = Field(default_factory=lambda: ['trees','water'], min_length=1, max_length=10)

def fetch_annual(box, year, width, height):
    try:
      cat=pystac_client.Client.open(STAC)
      items=cat.search(collections=[COLLECTION],bbox=box,datetime=f'{year}-07-01/{year}-07-02',max_items=20).item_collection()
      items=[i for i in items if str(i.properties.get('start_datetime','')).startswith(str(year))]
      if not items: raise HTTPException(404,f'Нет данных за {year} год для этой области')
      combined=np.zeros((height,width),dtype=np.uint8); used=[]
      coords=','.join(str(float(x)) for x in box)
      for item in items:
        url=f'{TILER}/{coords}/{width}x{height}.tif'
        r=requests.get(url,params={'collection':COLLECTION,'item':item.id,'assets':'data','return_mask':'false','resampling':'nearest'},timeout=90)
        r.raise_for_status()
        with MemoryFile(r.content) as m:
          with m.open() as ds: a=ds.read(1)
        if a.shape!=(height,width): raise ValueError('Неверный размер снимка')
        valid=np.isin(a,[1,2,4,5,7,8,9,11]); combined[(combined==0)&valid]=a[(combined==0)&valid]
        used.append(item.id)
      return combined,used
    except HTTPException: raise
    except Exception as e:
      logging.exception('Satellite data unavailable')
      raise HTTPException(503,'Источник спутниковых данных недоступен. Попробуйте позже или уменьшите область.') from e

@app.get('/api/v1/catalog', tags=['Автоматический анализ'])
def catalog():
    return {'source':'Impact Observatory / Esri Annual Land Cover (Sentinel-2)', 'years':{'min':2017,'max':2023}, 'categories':[{'id':k,'label':v[1], 'requires_comparison':k in ('forest_loss','forest_gain')} for k,v in LAND_CATEGORIES.items()], 'resolution_m':10}

@app.post('/api/v1/analyze', tags=['Автоматический анализ'])
def analyze_automatic(body: AreaRequest):
    w,s,e,n=body.bbox
    if not all(np.isfinite(body.bbox)) or not (-180<=w<e<=180 and -60<=s<n<=84): raise HTTPException(422,'Проверьте координаты области (широта от −60 до 84°)')
    if body.compare_year is not None and body.compare_year<body.year: raise HTTPException(422,'Год сравнения раньше первого года')
    if len(set(body.categories)) != len(body.categories) or any(x not in LAND_CATEGORIES for x in body.categories): raise HTTPException(422,'Выберите существующие категории без повторений')
    if body.compare_year is None and any(x in ('forest_loss','forest_gain') for x in body.categories): raise HTTPException(422,'Потеря и появление деревьев требуют двух лет')
    geod=Geod(ellps='WGS84');mid=(s+n)/2
    dx=abs(geod.inv(w,mid,e,mid)[2]);dy=abs(geod.inv(w,s,w,n)[2]);width=max(1,int(np.ceil(dx/10)));height=max(1,int(np.ceil(dy/10)))
    if width*height>1_000_000 or width>2000 or height>2000: raise HTTPException(422,'Слишком большая область. Выберите участок не больше примерно 100 км².')
    a,items1=fetch_annual(body.bbox,body.year,width,height)
    b,items2=fetch_annual(body.bbox,body.compare_year,width,height) if body.compare_year is not None else (None,[])
    valid=a>0
    if b is not None: valid &= b>0
    if not valid.any(): raise HTTPException(404,'Для выбранной области нет общих данных за указанные годы')
    # Ellipsoidal area per WGS84 pixel row, not degrees squared.
    ys=np.linspace(n,s,height+1); row_areas=np.empty(height,dtype='float64')
    for r in range(height):
      area,_=geod.polygon_area_perimeter([w,e,e,w],[ys[r],ys[r],ys[r+1],ys[r+1]])
      row_areas[r]=abs(area)/width/10000
    def hectares(mask): return float((mask.sum(axis=1)*row_areas).sum())
    total=hectares(valid);rows=[]
    for name in body.categories:
      code,label=LAND_CATEGORIES[name]
      if code is None:
        mask=valid & ((a==2)&(b!=2) if name=='forest_loss' else (a!=2)&(b==2));area=hectares(mask)
        rows.append({'class':name,'label':label,'area_ha':round(area,3),'percent':round(area/total*100,2)})
      else:
        h1=hectares(valid&(a==code));h2=hectares(valid&(b==code)) if b is not None else None
        rows.append({'class':name,'label':label,'before_ha':round(h1,3),'before_percent':round(h1/total*100,2),'after_ha':round(h2,3) if h2 is not None else None,'after_percent':round(h2/total*100,2) if h2 is not None else None,'change_ha':round(h2-h1,3) if h2 is not None else None,'relative_change_percent':round((h2-h1)/h1*100,2) if h2 is not None and h1 else None})
    out=np.where(valid,b if b is not None else a,0).astype('uint8')
    profile={'driver':'GTiff','height':height,'width':width,'count':1,'dtype':'uint8','crs':'EPSG:4326','transform':raster_from_bounds(*body.bbox,width,height),'nodata':0,'compress':'deflate'}
    with MemoryFile() as m:
      with m.open(**profile) as ds: ds.write(out,1)
      tif=m.read()
    key=str(uuid.uuid4());exports[key]=tif
    if len(exports)>20: exports.pop(next(iter(exports)))
    result = {'bbox':body.bbox,'years':[body.year,body.compare_year] if b is not None else [body.year], 'area_ha':round(total,3),'coverage_percent':round(float(valid.mean())*100,2),'rows':rows,'geotiff_url':'/api/export/'+key+'.tif','source':{'name':'Impact Observatory / Esri Annual Land Cover','collection':COLLECTION,'items':items1+items2,'resolution_m':10,'method':'Годовая классификация Sentinel-2 нейросетью'},'warning':'Годовые оценки деревьев и воды; деревья не всегда лес, вода не обязательно река. Ноль означает, что класс не обнаружен в выбранной области, но не доказывает его отсутствие. Смена класса может быть ошибкой модели.'}
    result['report_id']=key; reports[key]=result
    if len(reports)>20: reports.pop(next(iter(reports)))
    return result


# Scene-based forest analysis from the supplied ML-Project U-Net checkpoint.
from datetime import timedelta
from PIL import Image
from fastapi.responses import JSONResponse

class ForestRequest(BaseModel):
    bbox: list[float] = Field(min_length=4,max_length=4)
    before_date: date
    after_date: date

@app.get('/api/v1/geocode',tags=['Карта'])
def geocode(q: str):
    if len(q.strip())<2 or len(q)>100: raise HTTPException(422,'Введите название города')
    try:
      r=requests.get('https://nominatim.openstreetmap.org/search',params={'q':q,'format':'jsonv2','limit':5,'addressdetails':0},headers={'User-Agent':'TerraAnalysis/1.0 (local educational prototype)'},timeout=15);r.raise_for_status()
      return [{'name':x['display_name'],'lat':float(x['lat']),'lon':float(x['lon'])} for x in r.json()]
    except Exception as e: raise HTTPException(503,'Поиск городов временно недоступен') from e

def scene_for_date(box, when, width, height):
    coll='landsat-c2-l2' if when.year<2017 else 'sentinel-2-l2a'
    start=when-timedelta(days=45);end=when+timedelta(days=45)
    try:
      client=pystac_client.Client.open(STAC)
      items=list(client.search(collections=[coll],bbox=box,datetime=f'{start.isoformat()}/{end.isoformat()}',max_items=150).item_collection())
      items=[i for i in items if i.datetime and i.properties.get('eo:cloud_cover',100)<60]
      # Prefer a clear image near the requested date; reject highly clouded selections below.
      items.sort(key=lambda i:(abs((i.datetime.date()-when).days),float(i.properties.get('eo:cloud_cover',100))))
      if not items: raise HTTPException(404,f'Нет подходящих снимков около {when.isoformat()}')
      coords=','.join(str(float(v)) for v in box)
      assets=['red','green','blue','nir08','qa_pixel'] if coll.startswith('landsat') else ['B04','B03','B02','B08','SCL']
      for item in items[:6]:
        params=[('collection',coll),('item',item.id),('return_mask','false')]+[('assets',a) for a in assets]
        response=requests.get(f'{TILER}/{coords}/{width}x{height}.tif',params=params,timeout=90);response.raise_for_status()
        with MemoryFile(response.content) as mem:
          with mem.open() as ds: raw=ds.read()
        if raw.shape!=(5,height,width): continue
        if coll.startswith('landsat'):
          qa=raw[4].astype('uint16'); valid=((qa & 63)==0) & np.all(raw[:4]>0,axis=0)
          x=np.clip(raw[:4].astype('float32')*0.275-2000,0,10000)
        else:
          valid=np.isin(raw[4],[4,5,6]) & np.all(raw[:4]>0,axis=0)
          x=raw[:4].astype('float32')
        if valid.mean()<0.2:continue
        return x,valid,{'item':item.id,'date':item.datetime.date().isoformat(),'satellite':'Landsat' if coll.startswith('landsat') else 'Sentinel-2','cloud_cover_scene_percent':item.properties.get('eo:cloud_cover'),'valid_percent':round(valid.mean()*100,2)}
      raise HTTPException(404,f'Нет достаточно безоблачного снимка около {when.isoformat()}')
    except HTTPException: raise
    except Exception as e:
      logging.exception('Scene retrieval failed');raise HTTPException(503,'Не удалось получить спутниковые снимки') from e

def store_png(arr):
    output=io.BytesIO();Image.fromarray(arr).save(output,format='PNG');key=str(uuid.uuid4());exports[key]=output.getvalue()
    if len(exports)>30: exports.pop(next(iter(exports)))
    return '/api/v1/image/'+key+'.png'

@app.get('/api/v1/image/{name}',tags=['Изображения'])
def image_export(name:str):
    key=name.removesuffix('.png')
    if key not in exports:raise HTTPException(404,'Изображение недоступно')
    return Response(exports[key],media_type='image/png')

def mean_or_none(a, mask):
    return round(float(np.mean(a[mask])),3) if mask.any() else None

@app.post('/api/v1/forest',tags=['Лес U-Net'])
def forest_automatic(body:ForestRequest):
    w,s,e,n=body.bbox
    if not all(np.isfinite(body.bbox)) or not (-180<=w<e<=180 and -60<=s<n<=84):raise HTTPException(422,'Проверьте координаты')
    if body.before_date.year<2010 or body.after_date>date.today() or body.before_date>body.after_date:raise HTTPException(422,'Проверьте даты (начиная с 2010 года)')
    geod=Geod(ellps='WGS84');dx=abs(geod.inv(w,(s+n)/2,e,(s+n)/2)[2]);dy=abs(geod.inv(w,s,w,n)[2]);res=30 if body.before_date.year<2017 else 10
    width=max(1,int(np.ceil(dx/res)));height=max(1,int(np.ceil(dy/res)))
    if width*height>262144 or max(width,height)>512:raise HTTPException(422,'Выберите меньшую область: не более 512 пикселей по стороне')
    a,valid1,meta1=scene_for_date(body.bbox,body.before_date,width,height);b,valid2,meta2=scene_for_date(body.bbox,body.after_date,width,height)
    valid=valid1 & valid2
    if valid.mean()<.1:raise HTTPException(422,'Слишком мало общих безоблачных данных. Выберите другие даты.')
    p1=forest_predict(a);p2=forest_predict(b)
    if not np.isfinite(p1).all() or not np.isfinite(p2).all():raise HTTPException(500,'Модель вернула некорректный результат')
    forest1=(p1>.5)&valid;forest2=(p2>.5)&valid;loss=(p1>.5)&(p2<.35)&valid;stable=(p1>.5)&(p2>.5)&valid
    ys=np.linspace(n,s,height+1); areas=np.array([abs(geod.polygon_area_perimeter([w,e,e,w],[ys[i],ys[i],ys[i+1],ys[i+1]])[0])/width/10000 for i in range(height)])
    def area(mask):return round(float((mask.sum(axis=1)*areas).sum()),3)
    def ndvi(x):return (x[3]-x[0])/(x[3]+x[0]+1e-6)
    nd1,nd2=ndvi(a),ndvi(b)
    def rgb(x):return np.clip(np.moveaxis(x[:3],0,2)/4000*255,0,255).astype('uint8')
    pic1,pic2=rgb(a),rgb(b);overlay=pic2.copy();overlay[loss]=(0.35*overlay[loss]+0.65*np.array([240,60,44])).astype('uint8');overlay[~valid]=(0.4*overlay[~valid]).astype('uint8')
    urls={'before':store_png(pic1),'after':store_png(pic2),'loss':store_png(overlay)}
    result = {'bbox':body.bbox,'model':'ML-Project forest U-Net','scenes':[meta1,meta2],'resolution_m_approx':res,'common_coverage_percent':round(valid.mean()*100,2),'area_ha':area(valid),'forest_before_ha':area(forest1),'forest_after_ha':area(forest2),'forest_loss_ha':area(loss),'forest_stable_ha':area(stable),'ndvi':{'before_loss':mean_or_none(nd1,loss),'after_loss':mean_or_none(nd2,loss),'before_stable':mean_or_none(nd1,stable),'after_stable':mean_or_none(nd2,stable)},'images':urls,'warning':'Модель обучена в Японии и не проверена глобально. Сравнение со снимками Landsat экспериментальное. Красный цвет — предполагаемая потеря деревьев, а не подтверждённая вырубка.'}
    key=str(uuid.uuid4()); result['report_id']=key; reports[key]=result
    if len(reports)>20: reports.pop(next(iter(reports)))
    return result


@app.get('/api/v1/reports/{report_id}.pdf', tags=['Экспорт'])
def report_pdf(report_id: str, categories: str = '', lang: str = 'en'):
    from report_pdf import build_report
    from urllib.parse import unquote
    result = reports.get(report_id)
    if result is None: raise HTTPException(404, 'Отчёт больше не доступен. Повторите анализ.')
    selected = [x for x in categories.split(',') if x] if categories else None
    if selected is not None and 'rows' in result:
        if any(x not in {row['class'] for row in result['rows']} for x in selected): raise HTTPException(422, 'Категория не входит в результат')
        result = dict(result); result['rows'] = [row for row in result['rows'] if row['class'] in selected]
    try:
        pdf = build_report(result, exports, lang if lang in ('ru','en') else 'en')
    except Exception as e:
        logging.exception('PDF export failed');raise HTTPException(500, 'Не удалось сформировать PDF') from e
    return Response(pdf,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="terra-report.pdf"'})


@app.get('/api/v1/scenes', tags=['Спутниковые снимки'])
def available_scenes(bbox: str, year: int):
    try:
        box=json.loads(bbox)
        assert len(box)==4 and all(isinstance(n,(int,float)) and np.isfinite(n) for n in box)
        w,s,e,n=box
        assert -180<=w<e<=180 and -60<=s<n<=84 and 2010<=year<=date.today().year
    except (TypeError, ValueError, AssertionError): raise HTTPException(422,'Проверьте год и координаты')
    collection='landsat-c2-l2' if year<2017 else 'sentinel-2-l2a'
    try:
        client=pystac_client.Client.open(STAC)
        items=client.search(collections=[collection],bbox=box,datetime=f'{year}-01-01/{year}-12-31',max_items=300).item_collection()
        days={}
        for item in items:
            if not item.datetime: continue
            day=item.datetime.date().isoformat();cloud=item.properties.get('eo:cloud_cover')
            if day not in days or (cloud is not None and (days[day] is None or cloud<days[day])): days[day]=cloud
        return {'year':year,'satellite':'Landsat' if year<2017 else 'Sentinel-2','dates':[{'date':d,'cloud_cover_scene_percent':round(float(c),1) if c is not None else None} for d,c in sorted(days.items())], 'note':'Облачность указана для всей спутниковой сцены; условия на участке могут отличаться.'}
    except Exception as e:
        logging.exception('Scene search failed');raise HTTPException(503,'Не удалось получить даты съёмки') from e


# Scene-specific segmentation. Legacy /api/v1/analyze remains an annual-product endpoint.
class CoverSceneRequest(BaseModel):
    bbox: list[float] = Field(min_length=4,max_length=4)
    before_date: date
    after_date: date | None = None
    categories: list[str] = Field(default_factory=lambda:['trees','water'],min_length=1,max_length=10)

@app.post('/api/v2/cover', tags=['Покрытие по снимкам'])
def cover_scene_analysis(body: CoverSceneRequest):
    import cover_scene
    w,s,e,n=body.bbox
    if not all(np.isfinite(body.bbox)) or not (-180<=w<e<=180 and -60<=s<n<=84):raise HTTPException(422,'Проверьте координаты области')
    if body.before_date.year<2017 or (body.after_date and body.after_date.year<2017):raise HTTPException(422,'Анализ покрытия Landsat до 2017 года пока недоступен: для него нужна отдельная обученная модель.')
    if body.before_date>date.today() or (body.after_date and (body.after_date>date.today() or body.after_date<body.before_date)):raise HTTPException(422,'Проверьте порядок дат: будущие даты недоступны')
    if len(set(body.categories)) != len(body.categories) or any(x not in LAND_CATEGORIES for x in body.categories):raise HTTPException(422,'Проверьте список категорий')
    if body.after_date is None and any(x in ('forest_loss','forest_gain') for x in body.categories):raise HTTPException(422,'Потеря и появление деревьев требуют двух дат')
    geod=Geod(ellps='WGS84');dx=abs(geod.inv(w,(s+n)/2,e,(s+n)/2)[2]);dy=abs(geod.inv(w,s,w,n)[2]);width=max(1,int(np.ceil(dx/10)));height=max(1,int(np.ceil(dy/10)))
    if width*height>262144 or max(width,height)>512:raise HTTPException(422,'Слишком большая область. Выберите участок не больше 512 пикселей по стороне.')
    a,v1,meta1=cover_scene.fetch(body.bbox,body.before_date,width,height)
    b,v2,meta2=cover_scene.fetch(body.bbox,body.after_date,width,height) if body.after_date else (None,None,None)
    valid=v1.copy()
    if b is not None:valid &= v2
    if valid.mean()<.1:raise HTTPException(422,'Слишком мало общих безоблачных пикселей. Выберите другие даты.')
    ys=np.linspace(n,s,height+1); row_areas=np.array([abs(geod.polygon_area_perimeter([w,e,e,w],[ys[i],ys[i],ys[i+1],ys[i+1]])[0])/width/10000 for i in range(height)])
    def area(mask):return float((mask.sum(axis=1)*row_areas).sum())
    total=area(valid);codes={'water':0,'trees':1,'flooded_vegetation':2,'crops':3,'built_up':4,'bare_ground':5,'snow_ice':6,'rangeland':8};rows=[]
    for category in body.categories:
      label=LAND_CATEGORIES[category][1]
      if category in ('forest_loss','forest_gain'):
        m=valid & (((a==1)&(b!=1)) if category=='forest_loss' else ((a!=1)&(b==1)));h=area(m)
        rows.append({'class':category,'label':label,'area_ha':round(h,3),'percent':round(100*h/total,2)})
      else:
        h1=area(valid&(a==codes[category]));h2=area(valid&(b==codes[category])) if b is not None else None
        rows.append({'class':category,'label':label,'before_ha':round(h1,3),'before_percent':round(h1/total*100,2),'after_ha':round(h2,3) if h2 is not None else None,'after_percent':round(h2/total*100,2) if h2 is not None else None,'change_ha':round(h2-h1,3) if h2 is not None else None,'relative_change_percent':round((h2-h1)/h1*100,2) if h2 is not None and h1 else None})
    out=np.where(valid,b if b is not None else a,255).astype('uint8')
    profile={'driver':'GTiff','height':height,'width':width,'count':1,'dtype':'uint8','crs':'EPSG:4326','transform':raster_from_bounds(*body.bbox,width,height),'nodata':255,'compress':'deflate'}
    with MemoryFile() as mem:
      with mem.open(**profile) as dst:dst.write(out,1)
      tif=mem.read()
    key=str(uuid.uuid4());exports[key]=tif
    if len(exports)>20:exports.pop(next(iter(exports)))
    actual=[meta1['date']]+([meta2['date']] if meta2 else [])
    result={'bbox':body.bbox,'years':actual,'scenes_metadata':[x for x in [meta1,meta2] if x],'area_ha':round(total,3),'coverage_percent':round(float(valid.mean())*100,2),'rows':rows,'geotiff_url':'/api/export/'+key+'.tif','report_id':key,'source':{'name':'Blue Marble Landcover / Sentinel-2 L1C','resolution_m':10,'method':'Классификация отдельных спутниковых снимков','items':[x['item'] for x in [meta1,meta2] if x]},'warning':'Оценка нейросети для отдельных снимков; облака определяются моделью и могут быть пропущены. Деревья не обязательно лес. Сезонность и ошибки модели влияют на сравнение. Достоверность отдельных классов зависит от региона.'}
    reports[key]=result
    if len(reports)>20:reports.pop(next(iter(reports)))
    return result

@app.get('/api/openapi.json',include_in_schema=False)
def api_openapi():return app.openapi()

@app.get('/api/docs',include_in_schema=False)
def api_documentation():
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url='/api/openapi.json',title='Terra API')
