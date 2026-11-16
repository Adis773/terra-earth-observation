# Report service

This small Vercel deployment serves PDF generation separately from the model API. To deploy it, copy this directory into an empty deployment directory, then copy `../backend/report_pdf.py` to `backend/report_pdf.py` and `../backend/fonts/` to `backend/fonts/`. Deploy that directory with `vercel --prod`. The static frontend routing is configured in `../deploy/vercel.static.json`.
