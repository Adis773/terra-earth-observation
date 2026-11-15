# Forest Loss Detection from Sentinel-2 Imagery

Detects forest loss caused by landslides after the 2018 Hokkaido Eastern Iburi (Atsuma) earthquake. Model: U-Net.

## Data
- Sentinel-2 L2A via Microsoft Planetary Computer (STAC), bands B04, B03, B02, B08.
- Cloud masking with SCL, median composite for June to October.
- Training labels: io-lulc-annual-v02 land cover (output of another model, not manual labels), 6 regions of Japan.
- Validation: landslide inventory of the Iburi earthquake (Zhang et al., Zenodo 2577300).

## Results (Atsuma, 2018 to 2019)
- Forest share: 35.7% (2018) to 33.8% (2019) of valid pixels.
- NDVI in the loss zone: 0.87 to 0.48; where forest stayed: 0.87 to 0.87.
- Model-detected loss: 11.7 km2 (inventory: 20.3 km2).

## Validation against real landslides
Reference: 1,929 mapped landslides inside the frame (22.4% of the area). The model was trained on land cover only and never saw landslide labels.

| Loss rule | Precision | Recall | F1 | IoU |
|---|---|---|---|---|
| Original mask | 0.728 | 0.072 | 0.131 | 0.070 |
| pa > 0.5 and pb < 0.5 | 0.455 | 0.416 | 0.435 | 0.278 |
| pa > 0.5 and pb < 0.35 (chosen) | 0.641 | 0.371 | 0.470 | 0.307 |
| pa > 0.5 and pb < 0.2 | 0.760 | 0.300 | 0.430 | 0.274 |

Random guessing would give Precision of about 0.22 (landslide share of the frame). Removing small blobs and morphological closing did not improve the metrics (see clean.py).

![Final result](final_result.png)

## Limitations
- The threshold was chosen on the same data used for evaluation, so scores are slightly optimistic.
- The inventory was drawn from high-resolution imagery; small landslides are below the 10 m resolution.
- One region and one event; generalization is not tested.
- The "gained forest" area (~1.6 km2) is mostly noise and residual clouds.

## Run
python -m venv .venv
source .venv/bin/activate.fish
pip install numpy matplotlib scipy pyshp pyproj pillow
python metrics.py        # agreement with NDVI baseline
python gt_metrics.py     # validation against landslide inventory
python clean.py          # noise-removal experiment
python final.py          # final figure
Data and weights: Google Drive, folder "hackathon" (put into data/). Landslide inventory: Zenodo 2577300 (put into data/iburi/).