# Cover service

Deploy this directory with the root `backend/*.py` and `backend/fonts/` copied into a sibling `backend` directory. This deployment handles only `/api/v2/cover`; the forest API remains separate and requires PyTorch. The vendor directory contains the conda-forge Linux x86-64 libexpat 2.9.0 shared library and its license. It is loaded to support rasterio on the Vercel Python runtime.

The static site forwards `/api/v2/cover` to this service. The Python model and raster data may take a few minutes to load on cold starts; the free tier has usage and execution limits.
