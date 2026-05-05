# ORI Companion v2 — Albert School Groupe 1

Three-module orientation companion built on the real ORI Vertex AI Reasoning Engine.

## Modules
1. **Onboarding dialogue** — builds student profile through guided conversation
2. **Recommendation engine** — personalised formation suggestions based on profile
3. **Comparison module** — side-by-side comparison of up to 3 formations

## Quick start

```bash
# 1. Place credentials
cp letudiant-data-prod-albert.json credentials/

# 2. Install
pip install -r requirements.txt

# 3. Run
python app.py
```

- Full widget → http://localhost:5000
- Embeddable iframe → http://localhost:5000/embed

## Embedding on an external page

```html
<iframe src="http://your-server/embed" width="720" height="640"
  style="border:none;border-radius:16px"></iframe>
```

## UUID thread_id (per L'Étudiant instructions)

Each user gets a unique thread_id via Python uuid4(). 
The call is exactly: engine.query(config={"thread_id": uuid}, message=message)

## Security
- Never commit credentials/ to version control
- .gitignore already covers credentials/*.json
