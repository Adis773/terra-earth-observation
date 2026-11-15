# Terra API

The API accepts JSON and returns JSON with a UTF-8 content type. The experimental public endpoint is `https://barrier-norman-theaters-bouquet.trycloudflare.com`. It is hosted through a temporary tunnel and can go offline. For local use substitute `http://localhost:3187`. Open [Swagger UI](https://barrier-norman-theaters-bouquet.trycloudflare.com/api/docs) for the current generated schema.

## Before you start

- `bbox` is `[west, south, east, north]` in WGS84 longitude/latitude degrees. Bounds must be ordered and latitude must be between -60 and 84.
- Dates use `YYYY-MM-DD`. The service searches nearby acquisitions, not necessarily on the requested day. Actual dates are returned in scene metadata.
- Coverage is the percentage of common valid, cloud-masked pixels. Area is geodesically calculated and reported in hectares.
- Date-based analysis is limited to 512 pixels per side and 262,144 pixels total. Smaller areas and cloud-free scenes work best.
- `null` relative change means the baseline was zero. It is not a 0% change.
- Analysis can take minutes. Exports are temporary in-memory objects and disappear on restart or eviction.
- No API key is currently required. The demo proxy limits requests and concurrent analyses. Treat it as a prototype, not a production service.

## Health

`GET /api/health` returns `{ "status": "ok" }` when the Python service responds. It does not prove third-party catalogs are online.

## Available classes

`GET /api/v1/catalog` lists class IDs. Coverage classes are `water`, `trees`, `flooded_vegetation`, `crops`, `built_up`, `bare_ground`, `snow_ice`, and `rangeland`. Derived categories `forest_loss` and `forest_gain` require two dates. The catalog's year range describes the **annual** endpoint, not date-based coverage.

## Find available scenes

`GET /api/v1/scenes?bbox=[65.46,44.84,65.463,44.843]&year=2023`

Returns acquisition dates and scene-level cloud cover. It uses Landsat before 2017 and Sentinel-2 afterwards. Scene availability does not imply that every analysis model supports that sensor. Cloud cover is for the entire scene, not necessarily the selected area.

## Classify and compare land cover

`POST /api/v2/cover`

```json
{
  "bbox": [65.46, 44.84, 65.463, 44.843],
  "before_date": "2023-07-09",
  "after_date": "2026-07-01",
  "categories": ["trees", "water", "built_up", "forest_loss"]
}
```

`after_date` is optional for a single image; omit derived transition categories in that case. Only dates from 2017 are supported in this endpoint. Returns `years` (actual scene dates), `scenes_metadata`, `coverage_percent`, `area_ha`, `rows`, `geotiff_url`, `report_id`, `source`, and `warning`. Each normal row includes `before_ha`, `before_percent`, `after_ha`, `after_percent`, `change_ha` and `relative_change_percent`. Transition rows include `area_ha` and `percent` of common valid area.

GeoTIFF pixels: 0 water, 1 trees, 2 flooded vegetation, 3 crops, 4 built area, 5 bare ground, 6 snow/ice, 8 rangeland, 255 NoData. The model's cloud class is excluded. The export shows the latest analysed scene, not a transition map.

## Forest-change analysis

`POST /api/v1/forest`

```json
{
  "bbox": [141.94, 42.75, 141.945, 42.755],
  "before_date": "2018-07-01",
  "after_date": "2019-07-01"
}
```

Returns acquisition dates and scene metadata, `forest_before_ha`, `forest_after_ha`, estimated `forest_loss_ha`, `forest_stable_ha`, NDVI summaries and `images` URLs. Landsat is used before 2017, Sentinel-2 afterwards. The Japan-trained forest model has not been validated globally; Landsat use is experimental. Relative image URLs must be resolved against the API base URL.

## Historical annual product

`POST /api/v1/analyze` takes `bbox`, `year`, optional `compare_year` and `categories`. It uses the Impact Observatory annual 2017–2023 dataset, not an individual-date model. Output class codes differ from `/api/v2/cover`. Refer to the source metadata and schema before interpreting exports.

## Export results

- `GET /api/export/{id}.tif`: classification GeoTIFF.
- `GET /api/v1/reports/{report_id}.pdf?categories=trees,water`: PDF report; category filter applies to coverage reports.
- `GET /api/v1/image/{id}.png`: image from a forest response.

The browser also creates CSV and GeoJSON exports locally.

## Errors

| Status | Meaning |
| --- | --- |
| 404 | No suitable imagery, or an export has expired. |
| 422 | Invalid dates, coordinates, area, category or unsupported sensor/year. |
| 429 | Demo rate or concurrency limit reached; retry later. |
| 503 | An upstream catalog, tiler or model is temporarily unavailable. |

Error details may be in Russian in this prototype. Requests are logged by the hosting server and the external satellite-data providers. Coordinates are sent to these providers to retrieve the chosen area. Results are estimates and require independent validation.
