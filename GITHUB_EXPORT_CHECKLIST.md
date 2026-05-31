# GitHub Export Checklist

Use this checklist before publishing CoolRun AI as a new GitHub repository.

## Keep Private

- Do not commit `.env`.
- Keep `INFRARED_API_KEY`, `OPENROUTESERVICE_API_KEY`, `ROBOFLOW_API_KEY`, and `MAPBOX_TOKEN` only in local or deployment environment variables.
- Do not commit generated `data/` and `outputs/` files unless you intentionally create a small public demo dataset.
- Do not commit virtual environments or local caches: `.venv/`, `.venv311/`, `.uv-cache/`, `.uv-python/`.

## Safe To Commit

- `backend/`
- `frontend/`
- `notebooks/`
- `requirements.txt`
- `.env.example`
- `.gitignore`
- `.gitattributes`
- `README.md`
- `data/.gitkeep`
- `outputs/.gitkeep`

## Recommended First Commit

From the project root:

```powershell
git init
git add .
git status
git commit -m "Initial CoolRun AI demo"
git branch -M main
git remote add origin https://github.com/<your-username>/coolrun-ai.git
git push -u origin main
```

Before pushing, check `git status` and confirm `.env`, generated imagery, generated UTCI files, and virtual environments are not staged.

## Public Deployment

For a public demo, deploy the backend separately from the frontend or let FastAPI serve the frontend. Set these variables on the backend host:

```text
INFRARED_API_KEY
OPENROUTESERVICE_API_KEY
ROBOFLOW_API_KEY
ROBOFLOW_MODEL_ID
```

Never put these values in `frontend/app.js`, `frontend/index.html`, or committed documentation.
