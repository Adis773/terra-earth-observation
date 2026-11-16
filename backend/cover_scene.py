"""Date-specific Sentinel-2 L1C land-cover inference."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import lru_cache
import logging, time
import numpy as np
import requests
import pystac_client
from fastapi import HTTPException
from rasterio.io import MemoryFile
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
    result.sort(key=lambda i:(abs((i.datetime.date()-d).days),i.properties.get('eo:cloud_cover',100)))
    return result

def fetch(box,when,width,height):
    if when.year<2017: raise HTTPException(422,'Для выбранной даты поканальная модель покрытия пока недоступна: Landsat 2010–2016 требует отдельной обученной модели.')
    try:
      items=candidates(tuple(box),when.isoformat())
      if not items: raise HTTPException(404,'Подходящий снимок Sentinel-2 не найден рядом с '+when.isoformat())
      coords=','.join(str(float(x)) for x in box)
      endpoint=f'{TILER}/{coords}/{width}x{height}.tif'
      read_failures=0
      decoded_scenes=0
      for item in items[:3]:
        def read_band(name):
          raw=item.assets[name].href
          if raw.startswith('s3://sentinel-s2-l2a/'):
            # Some Earth Search L1C metadata labels this bucket as L2A. The L1C object is in the L1C bucket.
            raw=raw.replace('s3://sentinel-s2-l2a/','https://sentinel-s2-l1c.s3.amazonaws.com/',1)
          elif raw.startswith('s3://sentinel-s2-l1c/'):
            raw=raw.replace('s3://sentinel-s2-l1c/','https://sentinel-s2-l1c.s3.amazonaws.com/',1)
          if not raw.startswith('https://sentinel-s2-l1c.s3.amazonaws.com/'):
            raise ValueError('Неподдерживаемый источник канала Sentinel-2 L1C')
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
        if bands.shape!=(13,height,width): raise ValueError('Неверные размеры каналов')
        valid=np.all(bands>0,axis=0)
        if valid.mean()<.2: continue
        baseline=float(item.properties.get('s2:processing_baseline','0'))
        offset=1000 if baseline>=4 else 0
        mask=landcover.predict(bands,offset)
        valid &= mask!=7
        if valid.mean()<.2:continue
        return mask,valid,{'item':item.id,'date':item.datetime.date().isoformat(),'satellite':'Sentinel-2 L1C','cloud_cover_scene_percent':item.properties.get('eo:cloud_cover'),'valid_percent':round(float(valid.mean())*100,2),'model':'Blue Marble Landcover medium','baseline':str(item.properties.get('s2:processing_baseline','unknown'))}
      if read_failures and not decoded_scenes:
        raise HTTPException(503,'Не удалось получить снимки из внешнего сервиса. Это ошибка источника данных, а не обязательно облачность. Попробуйте позже или выберите другую дату.')
      if read_failures:
        raise HTTPException(503,'Некоторые снимки не удалось получить, а в остальных недостаточно пригодных пикселей. Попробуйте позже или выберите другую дату.')
      raise HTTPException(404,'На доступных снимках недостаточно пригодных пикселей для даты '+when.isoformat()+'. Выберите другую дату или область.')
    except HTTPException:raise
    except Exception as exc:
      logging.exception('L1C scene inference failed')
      raise HTTPException(503,'Не удалось получить каналы или выполнить модель покрытия. Повторите запрос позже.') from exc
