import requests, time
import os
BASE = os.environ.get("ORI_BASE", "http://localhost:5001")

s = requests.post(f"{BASE}/api/session", json={}).json()
tid = s["thread_id"]
print(f"Thread: {tid}\n")

prompts = [
    "Salut, je suis en Terminale et j'hesite entre commerce et ingenieur",
    "Je code depuis le college, j'aime les maths mais je suis pas sur d'aimer la prepa",
    "C'est quoi la difference entre un BUT et une licence info ?",
    "Et l'alternance, c'est compatible avec une ecole d'ingenieur ?",
    "Je veux travailler dans la tech, ideal en startup, qu'est-ce qui me correspond ?",
    "Tu peux me faire un bilan complet de ce que tu as compris de mon profil ?",
    "Et si je voulais combiner code et entrepreneuriat ?",
    "Parcoursup ferme quand exactement cette annee ?",
]
for i, p in enumerate(prompts, 1):
    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/api/chat", json={"thread_id": tid, "message": p}, timeout=60)
        dt = time.time() - t0
        if r.status_code == 429:
            d = r.json()
            retry_after = d.get("retry_after", 10)
            print(f"[{i}] RATE LIMITED — waiting {retry_after}s...")
            time.sleep(retry_after)
            # Retry once
            t0 = time.time()
            r = requests.post(f"{BASE}/api/chat", json={"thread_id": tid, "message": p}, timeout=60)
            dt = time.time() - t0
        if r.status_code != 200:
            print(f"[{i}] FAIL {r.status_code} in {dt:.1f}s: {r.text[:200]}")
        else:
            d = r.json()
            reply = d.get("reply", "")
            tin = d['tokens']['input']; tout = d['tokens']['output']
            print(f"[{i}] OK {dt:.1f}s tok_in={tin} tok_out={tout} len={len(reply)} preview={reply[:90]!r}")
    except Exception as e:
        print(f"[{i}] EXCEPTION {time.time()-t0:.1f}s: {e}")
    time.sleep(2)
