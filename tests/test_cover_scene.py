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
        item=SimpleNamespace(assets={band:SimpleNamespace(href='s3://sentinel-s2-l1c/test.jp2') for band in cover_scene.BANDS})
        with patch('cover_scene.candidates',return_value=[item]), patch('cover_scene.requests.get',side_effect=RuntimeError('upstream unavailable')):
            with self.assertRaises(HTTPException) as error:
                cover_scene.fetch((0,0,.01,.01),date(2018,7,1),3,3)
        self.assertEqual(error.exception.status_code,503)
        self.assertNotIn('2018-07-01',error.exception.detail)
    def test_pre_sentinel_date_is_rejected(self):
        self.body['before_date']='2010-01-01'
        self.assertEqual(self.client.post('/api/v2/cover',json=self.body).status_code,422)
    def test_forest_loss_requires_two_images(self):
        self.body.pop('after_date');self.body['categories']=['forest_loss']
        self.assertEqual(self.client.post('/api/v2/cover',json=self.body).status_code,422)

if __name__=='__main__':unittest.main()
