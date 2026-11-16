import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import app

class CoverSceneTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app.app)
        self.body={'bbox':[0,0,.00026,.00026],'before_date':'2022-07-01','after_date':'2023-07-01','categories':['water','trees','flooded_vegetation','crops','built_up','bare_ground','snow_ice','rangeland','forest_loss','forest_gain']}
    def test_all_categories_and_forest_transition(self):
        first=np.array([[0,1,2],[3,4,5],[6,8,1]],dtype=np.uint8)
        second=np.array([[0,3,2],[3,4,5],[6,8,1]],dtype=np.uint8)
        def fetch(box,when,width,height):
            self.assertEqual((width,height),(3,3))
            return (first if when.year==2022 else second),np.ones((3,3),bool),{'date':when.isoformat(),'item':'test','satellite':'Sentinel-2'}
        with patch('cover_scene.fetch',side_effect=fetch):
            response=self.client.post('/api/v2/cover',json=self.body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['selected_area_ha'],response.json()['area_ha'])
        self.assertEqual(response.json()['unobserved_area_ha'],0)
        rows={r['class']:r for r in response.json()['rows']}
        for category in self.body['categories'][:8]: self.assertGreater(rows[category]['before_ha'],0,category)
        self.assertGreater(rows['forest_loss']['area_ha'],0)
        self.assertEqual(rows['forest_gain']['area_ha'],0)
        self.assertIsNotNone(rows['trees']['change_percentage_points'])
        overlays=response.json()['map_overlays']
        self.assertTrue(overlays['before']['water'].startswith('data:image/png;base64,'))
        self.assertTrue(overlays['change']['forest_loss'].startswith('data:image/png;base64,'))
        self.assertIsNone(overlays['change']['forest_gain'])
        import base64
        self.assertTrue(base64.b64decode(response.json()['geotiff_base64']).startswith((b'II',b'MM')))
        result=response.json();result.pop('geotiff_base64')
        app.exports.clear();app.reports.clear()
        pdf=self.client.post('/api/v1/reports/generate',json={'result':result,'categories':['trees','water'],'language':'en'})
        self.assertEqual(pdf.status_code,200,pdf.text[:300]);self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.assertIn(b'/Subtype /Image', pdf.content, 'Map images should appear in the report')
    def test_upstream_failure_is_not_reported_as_clouds(self):
        from datetime import date
        from types import SimpleNamespace
        from fastapi import HTTPException
        import cover_scene
        item=SimpleNamespace(datetime=__import__('datetime').datetime(2018,7,1),assets={band:SimpleNamespace(href='s3://sentinel-s2-l1c/test.jp2') for band in cover_scene.BANDS})
        with patch('cover_scene.candidates',return_value=[item]), patch('cover_scene.rasterio.open',side_effect=RuntimeError('archive unavailable')), patch('cover_scene.requests.get',side_effect=RuntimeError('upstream unavailable')):
            with self.assertRaises(HTTPException) as error:
                cover_scene.fetch((0,0,.01,.01),date(2018,7,1),3,3)
        self.assertEqual(error.exception.status_code,503)
        self.assertNotIn('2018-07-01',error.exception.detail)
    def test_adjacent_scenes_fill_cloud_gaps_with_context(self):
        from datetime import date, datetime
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        import cover_scene
        def item(day):
            return SimpleNamespace(id=f'scene-{day}', datetime=datetime(2018,7,day),properties={'s2:processing_baseline':'02.06','eo:cloud_cover':1},assets={band:SimpleNamespace(href='s3://sentinel-s2-l1c/test.jp2') for band in cover_scene.BANDS})
        source=MagicMock();vrt=MagicMock();vrt.__enter__.return_value.read.return_value=np.full((256,256),1000,dtype=np.uint16)
        first=np.ones((256,256),dtype=np.uint8);first[126:128,126:130]=7
        second=np.ones((256,256),dtype=np.uint8)
        with patch('cover_scene.candidates',return_value=[item(1),item(4)]), patch('cover_scene.rasterio.open',return_value=source), patch('cover_scene.WarpedVRT',return_value=vrt), patch('cover_scene.landcover.predict',side_effect=[first,second]) as predictor:
            mask,valid,meta=cover_scene.fetch((0,0,.001,.001),date(2018,7,1),4,4)
        self.assertTrue(valid.all());self.assertTrue((mask==1).all())
        self.assertEqual([x['date'] for x in meta['observations']],['2018-07-01','2018-07-04'])
        self.assertEqual(predictor.call_args_list[0].args[0].shape,(13,256,256))

    def test_historical_cache_reuses_results_without_shared_mutation(self):
        from datetime import date
        import cover_scene
        cover_scene._cached_fetch.cache_clear()
        original=(np.ones((2,2),dtype=np.uint8),np.ones((2,2),dtype=bool),{'date':'2018-07-01'})
        try:
            with patch('cover_scene._fetch_uncached',return_value=original) as read:
                mask,valid,meta=cover_scene.fetch([1,2,3,4],date(2018,7,1),2,2)
                mask[0,0]=0;valid[0,0]=False;meta['date']='changed'
                mask,valid,meta=cover_scene.fetch([1,2,3,4],date(2018,7,1),2,2)
                self.assertEqual(read.call_count,1)
                self.assertEqual(mask[0,0],1);self.assertTrue(valid[0,0]);self.assertEqual(meta['date'],'2018-07-01')
        finally: cover_scene._cached_fetch.cache_clear()

    def test_pre_sentinel_date_is_rejected(self):
        self.body['before_date']='2010-01-01'
        self.assertEqual(self.client.post('/api/v2/cover',json=self.body).status_code,422)
    def test_forest_loss_requires_two_images(self):
        self.body.pop('after_date');self.body['categories']=['forest_loss']
        self.assertEqual(self.client.post('/api/v2/cover',json=self.body).status_code,422)

if __name__=='__main__':unittest.main()
