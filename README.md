# CoolRun AI Merged Vegetation Demo

This folder contains only the public merged-vegetation frontend demo.

It uses:

- Infrared SDK / OSM vegetation
- Roboflow AI detected trees
- merged vegetation UTCI analysis
- route scoring from the backend

## Run Locally

Start the CoolRun backend from the full project:

```powershell
cd "C:\Users\dalam\OneDrive\Masaüstü\coolrun-ai"
.\.venv311\Scripts\Activate.ps1
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Serve this folder:

```powershell
cd "C:\Users\dalam\OneDrive\Masaüstü\coolrun-ai\github_merged_demo"
python -m http.server 5500
```

Open:

```text
http://localhost:5500
```

## Publish As A New GitHub Repository

From this folder:

```powershell
cd "C:\Users\dalam\OneDrive\Masaüstü\coolrun-ai\github_merged_demo"
git init
git add .
git commit -m "Publish CoolRun AI merged vegetation demo"
git branch -M main
git remote add origin https://github.com/<your-username>/<new-repository-name>.git
git push -u origin main
```

## Important

This static frontend calls:

```text
http://127.0.0.1:8001
```

For a public deployment, change `API_BASE` in `merged_utci_demo.js` to your deployed backend URL. Do not put API keys in this frontend repository.
