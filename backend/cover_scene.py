"""Date-specific Sentinel-2 L1C land-cover inference."""
import raster_bootstrap  # Must run before importing rasterio.
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import lru_cache
import logging, time
import numpy as np
import requests
import pystac_client
from fastapi import HTTPException
from rasterio.io import MemoryFile
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.transform import from_bounds
import landcover

EARTH='https://earth-search.aws.element84.com/v1'
TILER='https://titiler.xyz/cog/bbox'
BANDS=['coastal','blue','green','red','rededge1','rededge2','rededge3','nir','nir08','nir09','cirrus','swir16','swir22']

@lru_cache(maxsize=32)
def candidates(box, date_iso):
    from datetime import date
    d=date.fromisoformat(date_iso)
    client=pystac_client.Client.open(EARTH)
    start=d-timedelta(days=45);end=d+timedelta(days=45)
    result=list(client.search(collections=['sentinel-2-l1c'],bbox=list(box),datetime=f'{start}/{end}',max_items=200).item_collection())
    result=[i for i in result if i.datetime and i.properties.get('eo:cloud_cover',100)<60 and all(b in i.assets for b in BANDS)]
    result.sort(key=lambda i:(abs((i.datetime.date()-d).days)//10,i.properties.get('eo:cloud_cover',100),abs((i.datetime.date()-d).days)))
    return result

def fetch(box,when,width,height):
    if when.year<2017: raise HTTPException(422,'Для выбранной даты поканальная модель покрытия пока недоступна: Landsat 2010–2016 требует отдельной обученной модели.')
    try:
      items=candidates(tuple(box),when.isoformat())
      if not items: raise HTTPException(404,'Подходящий снимок Sentinel-2 не найден рядом с '+when.isoformat())
      # The model was trained with spatial context. Extend small requests to a
      # full 256-pixel scene instead of repeating the area edge during inference.
      ctx_width=max(256,width);ctx_height=max(256,height)
      left=(ctx_width-width)//2;top=(ctx_height-height)//2
      dx=(box[2]-box[0])/width;dy=(box[3]-box[1])/height
      context_box=(box[0]-left*dx,box[1]-(ctx_height-height-top)*dy,box[2]+(ctx_width-width-left)*dx,box[3]+top*dy)
      if context_box[0]<-180 or context_box[2]>180 or context_box[1]<-90 or context_box[3]>90:
        context_box=box;ctx_width=width;ctx_height=height;left=top=0
      coords=','.join(str(float(x)) for x in context_box)
      endpoint=f'{TILER}/{coords}/{ctx_width}x{ctx_height}.tif'
      composite=None;composite_valid=None;observations=[];used_dates=set()
      read_failures=0
      decoded_scenes=0
      for item in items[:6]:
        acquired=item.datetime.date().isoformat()
        if acquired in used_dates: continue
        if len(observations)>=2: break
        def read_band(name):
          raw=item.assets[name].href
          if raw.startswith('s3://sentinel-s2-l2a/'):
            # Some Earth Search L1C metadata labels this bucket as L2A. The L1C object is in the L1C bucket.
            raw=raw.replace('s3://sentinel-s2-l2a/','https://sentinel-s2-l1c.s3.amazonaws.com/',1)
          elif raw.startswith('s3://sentinel-s2-l1c/'):
            raw=raw.replace('s3://sentinel-s2-l1c/','https://sentinel-s2-l1c.s3.amazonaws.com/',1)
          if not raw.startswith('https://sentinel-s2-l1c.s3.amazonaws.com/'):
            raise ValueError('Неподдерживаемый источник канала Sentinel-2 L1C')
          # Read the actual L1C JP2 directly from the public AWS archive. WarpedVRT
          # puts every band on the same WGS84 pixel grid, including edge pixels.
          try:
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', GDAL_HTTP_TIMEOUT='90', GDAL_HTTP_MAX_RETRY='2'):
              with rasterio.open(raw) as src:
                with WarpedVRT(src,crs='EPSG:4326',transform=from_bounds(*context_box,ctx_width,ctx_height),width=ctx_width,height=ctx_height,resampling=rasterio.enums.Resampling.nearest) as vrt:
                  return vrt.read(1)
          except Exception:
            logging.warning('Direct archive read failed; trying image service',exc_info=True)
            r=requests.get(endpoint,params={'url':raw,'return_mask':'false','resampling':'nearest'},timeout=90);r.raise_for_status()
            with MemoryFile(r.content) as mem:
              with mem.open() as ds:return ds.read(1)
        try:
          with ThreadPoolExecutor(max_workers=4) as pool:
            bands=np.stack(list(pool.map(read_band,BANDS)))
        except Exception:
          read_failures+=1
          logging.warning('Unable to read one scene, trying another',exc_info=True)
          continue
        decoded_scenes+=1
        if bands.shape!=(13,ctx_height,ctx_width): raise ValueError('Invalid band dimensions')
        data_valid=np.all(bands>0,axis=0)
        baseline=float(item.properties.get('s2:processing_baseline','0'))
        offset=1000 if baseline>=4 else 0
        mask=landcover.predict(bands,offset)
        region=np.s_[top:top+height,left:left+width]
        mask=mask[region];valid=data_valid[region] & (mask!=7)
        if not valid.any(): continue
        if composite is None:
          composite=mask.copy();composite_valid=valid.copy();new_pixels=valid
        else:
          new_pixels=valid & ~composite_valid
          if not new_pixels.any(): continue
          composite[new_pixels]=mask[new_pixels];composite_valid |= valid
        used_dates.add(acquired)
        observations.append({'date':acquired,'item':item.id,'added_percent':round(float(new_pixels.mean())*100,2),'cloud_cover_scene_percent':item.properties.get('eo:cloud_cover')})
        if composite_valid.mean()>=.98:break
      if composite is not None and composite_valid.mean()>=.2:
        first=observations[0]
        return composite,composite_valid,{'item':first['item'],'date':first['date'],'satellite':'Sentinel-2 L1C','cloud_cover_scene_percent':first['cloud_cover_scene_percent'],'valid_percent':round(float(composite_valid.mean())*100,2),'model':'Blue Marble Landcover medium','imagery_access':'AWS Sentinel-2 L1C archive','observations':observations,'composite':len(observations)>1}
      if read_failures and not decoded_scenes:
        raise HTTPException(503,'Не удалось получить снимки из внешнего сервиса. Это ошибка источника данных, а не обязательно облачность. Попробуйте позже или выберите другую дату.')
      if read_failures:
        raise HTTPException(503,'Некоторые снимки не удалось получить, а в остальных недостаточно пригодных пикселей. Попробуйте позже или выберите другую дату.')
      raise HTTPException(404,'На доступных снимках недостаточно пригодных пикселей для даты '+when.isoformat()+'. Выберите другую дату или область.')
    except HTTPException:raise
    except Exception as exc:
      logging.exception('L1C scene inference failed')
      raise HTTPException(503,'Не удалось получить каналы или выполнить модель покрытия. Повторите запрос позже.') from exc
