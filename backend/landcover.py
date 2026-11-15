"""Sentinel-2 L1C land-cover comparison API."""
import logging
import threading
from functools import lru_cache
import numpy as np
import onnxruntime as ort
import rasterio
from rasterio.io import MemoryFile
from rasterio.windows import Window
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from huggingface_hub import hf_hub_download

app = FastAPI(title="Sentinel-2 Land Change API", version="2.0")
log = logging.getLogger(__name__)
MODEL = "Civimatica/blue-marble-landcover"
REVISION = "main"
CLASSES = ["water", "trees", "flooded_vegetation", "crops", "built_up", "bare_ground", "snow_ice", "clouds", "rangeland"]
BANDS = ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B10", "B11", "B12"]
MEAN = np.array([2357.089,2137.385,2018.788,2082.986,2295.651,2854.537,3122.849,3040.560,3306.481,1473.847,506.070,2472.825,1838.929], dtype=np.float32)[:,None,None]
STD = np.array([1624.683,1675.806,1557.708,1833.702,1823.738,1733.977,1732.131,1679.732,1727.26,1024.687,442.165,1331.411,1160.419], dtype=np.float32)[:,None,None]
MAX_FILE = 200 * 1024 * 1024
MAX_PIXELS = 4_000_000
TILE = 256
lock = threading.Lock()

@lru_cache(maxsize=1)
def get_session():
    path = hf_hub_download(repo_id=MODEL, filename="medium/model_fp32.onnx", revision=REVISION)
    return ort.InferenceSession(path, providers=["CPUExecutionProvider"])

def read_file(upload):
    data = upload.file.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise HTTPException(413, "Каждый файл должен быть не больше 200 МБ")
    return data

def open_raster(data):
    try:
        mem = MemoryFile(data)
        ds = mem.open()
        if ds.count != 13:
            raise ValueError("Нужен GeoTIFF с 13 каналами Sentinel-2 L1C")
        if not ds.crs or not ds.crs.is_projected:
            raise ValueError("Нужна проекционная CRS в метрах, например UTM")
        if ds.crs.linear_units.lower() not in ("metre", "meter", "metres", "meters", "m"):
            raise ValueError("Единицы CRS должны быть метрами")
        if ds.width * ds.height > MAX_PIXELS:
            raise ValueError("Максимальный размер снимка — 4 млн пикселей")
        if ds.transform.a * ds.transform.e - ds.transform.b * ds.transform.d == 0:
            raise ValueError("Некорректная геопривязка")
        return mem, ds
    except Exception as exc:
        if 'ds' in locals(): ds.close()
        if 'mem' in locals(): mem.close()
        raise HTTPException(422, f"Некорректный GeoTIFF: {exc}") from exc

def predict(bands, offset):
    h,w = bands.shape[1:]
    result = np.empty((h,w), dtype=np.uint8)
    session = get_session()
    for top in range(0,h,TILE):
        for left in range(0,w,TILE):
            part = bands[:,top:top+TILE,left:left+TILE].astype(np.float32)
            ph,pw = part.shape[1:]
            # Pad with edge pixels so every tile matches the model's 256 x 256 input.
            part = np.pad(part, ((0,0),(0,TILE-ph),(0,TILE-pw)), mode="edge")
            part = (np.maximum(part - offset, 0) - MEAN) / STD
            with lock:
                logits = session.run(None, {"image": part[None].astype(np.float32)})[0]
            result[top:top+ph,left:left+pw] = np.argmax(logits[0,:,:ph,:pw],axis=0)
    return result

@app.get("/health")
def health():
    return {"status":"ok", "model":MODEL, "model_loaded":get_session.cache_info().currsize > 0}

@app.post("/compare")
def compare(before: UploadFile = File(...), after: UploadFile = File(...),
            before_date: str = Form(...), after_date: str = Form(...),
            before_offset: int = Form(...), after_offset: int = Form(...),
            x: float = Form(0, ge=0, lt=1), y: float = Form(0, ge=0, lt=1),
            width: float = Form(1, gt=0, le=1), height: float = Form(1, gt=0, le=1)):
    from datetime import date
    try:
        date_before, date_after = date.fromisoformat(before_date), date.fromisoformat(after_date)
    except ValueError:
        raise HTTPException(422, "Даты должны быть в формате YYYY-MM-DD")
    if date_after < date_before:
        raise HTTPException(422, "Вторая дата раньше первой")
    if before_offset not in (0,1000) or after_offset not in (0,1000):
        raise HTTPException(422, "Смещение каждого снимка должно быть 0 или 1000")
    if x + width > 1 + 1e-9 or y + height > 1 + 1e-9:
        raise HTTPException(422, "Прямоугольник выходит за пределы снимка")
    mem1, ds1 = open_raster(read_file(before))
    try:
        mem2, ds2 = open_raster(read_file(after))
        try:
            if ds1.crs != ds2.crs or ds1.transform != ds2.transform or (ds1.width,ds1.height) != (ds2.width,ds2.height):
                raise HTTPException(422, "Снимки должны иметь одинаковые CRS, сетку, размеры и геопривязку")
            l,t = round(x*ds1.width),round(y*ds1.height)
            r,b = round((x+width)*ds1.width),round((y+height)*ds1.height)
            if r<=l or b<=t: raise HTTPException(422, "Область меньше одного пикселя")
            window = Window(l,t,r-l,b-t)
            a, z = ds1.read(window=window, masked=True), ds2.read(window=window, masked=True)
            valid = ~np.any(np.ma.getmaskarray(a) | np.ma.getmaskarray(z), axis=0)
            valid &= np.all(np.isfinite(a.data) & np.isfinite(z.data), axis=0)
            if not np.any(valid): raise HTTPException(422, "Нет общих корректных пикселей")
            try:
                p1, p2 = predict(a.filled(0), before_offset), predict(z.filled(0), after_offset)
            except Exception as exc:
                log.exception("Model inference failed")
                raise HTTPException(503, "Ошибка загрузки или запуска модели. Проверьте интернет и журнал сервера.") from exc
            valid &= (p1 != 7) & (p2 != 7)
            count = int(valid.sum())
            if count == 0: raise HTTPException(422, "Не осталось общих безоблачных пикселей")
            pixel_ha = abs(ds1.transform.a*ds1.transform.e-ds1.transform.b*ds1.transform.d)/10000
            rows=[]
            for idx,name in enumerate(CLASSES):
                if name == "clouds": continue
                n1,n2=int(np.sum((p1==idx)&valid)),int(np.sum((p2==idx)&valid))
                rows.append({"class":name,"before_hectares":round(n1*pixel_ha,4),"after_hectares":round(n2*pixel_ha,4),"change_hectares":round((n2-n1)*pixel_ha,4),"before_percent":round(n1/count*100,3),"after_percent":round(n2/count*100,3),"change_percentage_points":round((n2-n1)/count*100,3),"relative_change_percent":round((n2-n1)/n1*100,3) if n1 else None})
            return {"model":MODEL,"dates":{"before":before_date,"after":after_date},"crs":str(ds1.crs),"pixel_area_hectares":pixel_ha,"selected_pixels":int(valid.size),"compared_pixels":count,"excluded_pixels":int(valid.size-count),"compared_area_hectares":round(count*pixel_ha,4),"results":rows,"notice":"Оценка на общей безоблачной площади; деревья не обязательно являются лесом."}
        finally:
            ds2.close();mem2.close()
    finally:
        ds1.close();mem1.close()
