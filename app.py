import os, json, uuid, re, io, time, logging, threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from flask import Flask, request, jsonify, render_template, send_file

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(
    os.path.dirname(__file__), "credentials", "letudiant-data-prod-albert.json"
)

import vertexai
from vertexai.preview import reasoning_engines

PROJECT_ID          = "letudiant-data-prod"
REASONING_ENGINE_ID = "7428309353347678208"
LOCATION            = "europe-west1"

app = Flask(__name__)
vertexai.init(project=PROJECT_ID, location=LOCATION)
engine = reasoning_engines.ReasoningEngine(REASONING_ENGINE_ID)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ori")

sessions: dict = {}
_last_query_time: dict = {}  # thread_id -> timestamp of last successful query

# ── Thread pool for timeout ──────────────────────────────────────────────────
_executor = ThreadPoolExecutor(max_workers=4)

def _query_with_timeout(thread_id, message, timeout_s=25):
    fut = _executor.submit(engine.query, config={"thread_id": thread_id}, message=message)
    return fut.result(timeout=timeout_s)

# ── Circuit breaker for Vertex rate-limit ────────────────────────────────────
_rate_state = {
    "recent_429s": deque(maxlen=10),
    "tripped_until": 0.0,
    "lock": threading.Lock(),
}

def _circuit_check():
    now = time.time()
    with _rate_state["lock"]:
        if now < _rate_state["tripped_until"]:
            return False, int(_rate_state["tripped_until"] - now) + 1
        return True, 0

def _circuit_record_429():
    now = time.time()
    with _rate_state["lock"]:
        _rate_state["recent_429s"].append(now)
        recent = [t for t in _rate_state["recent_429s"] if now - t < 60]
        if len(recent) >= 3:
            _rate_state["tripped_until"] = now + 45
            log.warning(f"CIRCUIT OPEN — {len(recent)} 429s in 60s, blocking for 45s")

# ── Debug log ────────────────────────────────────────────────────────────────
DEBUG_LOG = "/tmp/ori_debug.jsonl"

def _log_debug(thread_id, attempt, raw_str, parsed_text, meta):
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "thread_id": thread_id,
        "attempt": attempt,
        "raw_str_preview": str(raw_str)[:500],
        "parsed_text": parsed_text[:300],
        "meta": meta,
    }
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ── Parser ───────────────────────────────────────────────────────────────────
def parse_ori_response(raw) -> tuple[str, dict]:
    if isinstance(raw, dict):
        raw_str = (
            raw.get("output") or raw.get("message") or
            raw.get("text") or raw.get("content") or str(raw)
        )
    else:
        raw_str = str(raw)

    # Normalise U+241F to U+001F
    raw_str = raw_str.replace("\u241f", "\x1f")
    meta = {}

    if "\x1f" in raw_str:
        parts = raw_str.split("\x1f")

        # Extract trailing JSON metadata
        if parts:
            try:
                candidate = parts[-1].strip()
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and (
                    "input_tokens_count" in parsed or "output_tokens_count" in parsed
                ):
                    meta = parsed
                    parts = parts[:-1]
            except (json.JSONDecodeError, ValueError):
                pass

        # Remove leading single-char status (strict: exactly ^[A-Z]$ followed by separator)
        if parts and len(parts[0].strip()) == 1 and re.match(r'^[A-Z]$', parts[0].strip()):
            parts = parts[1:]

        text = " ".join(p.strip() for p in parts if p.strip())
    else:
        text = raw_str.strip()

    # Clean trailing JSON that might have leaked
    text = re.sub(r'\s*\{["\w]*tokens_count[^}]*\}\s*$', '', text).strip()
    # Remove stray separators
    text = text.replace("\x1f", " ").replace("\u241f", " ").strip()

    # Fallback: if text too short, return cleaned raw
    if len(text) < 5:
        log.warning(f"Parser fallback: parsed text too short ({len(text)} chars)")
        text = raw_str.replace("\x1f", " ").replace("\u241f", " ").strip()

    return text, meta


# ── Profile extraction ───────────────────────────────────────────────────────
PROGRAMMES = {
    "BTS": {
        "label": "BTS", "duration": "2 ans", "level": "Bac+2",
        "access": "Bac toutes series", "cost": "Gratuit (public)",
        "insertion": "72%", "salary_entry": "1 650 EUR", "parcoursup_rate": "55%",
    },
    "BUT": {
        "label": "BUT", "duration": "3 ans", "level": "Bac+3",
        "access": "Bac toutes series", "cost": "Gratuit (IUT public)",
        "insertion": "79%", "salary_entry": "1 780 EUR", "parcoursup_rate": "45%",
    },
    "Licence": {
        "label": "Licence", "duration": "3 ans", "level": "Bac+3",
        "access": "Bac selon filiere", "cost": "~170 EUR/an",
        "insertion": "58%", "salary_entry": "1 600 EUR", "parcoursup_rate": "80%",
    },
    "École de commerce": {
        "label": "Ecole de commerce", "duration": "3-5 ans", "level": "Bac+3 a Bac+5",
        "access": "Concours / admissions", "cost": "8 000-16 000 EUR/an",
        "insertion": "93%", "salary_entry": "2 400 EUR", "parcoursup_rate": "Concours",
    },
    "École d'ingénieurs": {
        "label": "Ecole d'ingenieurs", "duration": "3-5 ans", "level": "Bac+5",
        "access": "Prepa + concours", "cost": "2 500-10 000 EUR/an",
        "insertion": "96%", "salary_entry": "2 700 EUR", "parcoursup_rate": "Concours",
    },
}


def _normalize(s):
    import unicodedata
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").lower()

def _word_match(text, keywords):
    t_norm = _normalize(text)
    for kw in keywords:
        kw_norm = _normalize(kw)
        pattern = r'(?<![a-z])' + re.escape(kw_norm) + r'(?![a-z])'
        if re.search(pattern, t_norm):
            return True
    return False

def _extract_profile(sess, text):
    t = text.lower()
    p = sess["profile"]

    sectors = {
        "technologie / numerique": ["informatique","developpeur","ia","data","numerique","code","tech","algorithme","programmation","informaticien","logiciel"],
        "sante": ["medecin","infirmier","sante","medecine","pharmacie","kine","soins","paramedical","biologie","chirurgie"],
        "commerce / management": ["commerce","gestion","management","marketing","business","vente","finance","economie","comptabilite","rh"],
        "ingenierie": ["ingenieur","mecanique","electronique","physique","mathematiques","maths","cpge","prepa","sciences","robotique"],
        "arts / design": ["design","graphisme","beaux-arts","multimedia","dessin","illustration","photographie","mode","stylisme","animation"],
        "droit / sciences po": ["droit","juridique","avocat","sciences po","politique","notaire","magistrat"],
        "sport": ["football","sport","sportif","athletisme","basket","rugby","tennis","natation","entraineur","coach sportif"],
        "education / social": ["enseignant","professeur","education","social","psychologie","travailleur social","aide soignant"],
    }
    for sector, kws in sectors.items():
        if _word_match(t, kws):
            p["secteur_detecte"] = sector
            break

    levels = {
        "Terminale": ["terminale","lycee","parcoursup","premiere","seconde"],
        "Bac+1/2":   ["bts","iut","but","l1","deug"],
        "Bac+3+":    ["licence","master","bachelor","l3","reorientation","m1","m2"],
    }
    for lvl, kws in levels.items():
        if _word_match(t, kws):
            p["niveau"] = lvl
            break

    if _word_match(t, ["alternance","apprentissage"]):
        p["preference"] = "alternance / terrain"
    elif _word_match(t, ["recherche","doctorat","these"]):
        p["preference"] = "academique / recherche"

    if _word_match(t, ["decide","je veux","je sais","certain"]):
        p["maturite"] = "Avance"
    elif _word_match(t, ["hesite","pas sur","peut-etre","je ne sais"]):
        p["maturite"] = "En construction"
    else:
        p.setdefault("maturite", "Exploratoire")


def _detect_bilan(reply):
    keywords = ["Bilan", "profil detect", "Formations recommand", "Niveau de maturit"]
    return any(kw in reply for kw in keywords)


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/embed")
def embed():
    return render_template("embed.html")

@app.route("/api/session", methods=["POST"])
def new_session():
    thread_id = str(uuid.uuid4())
    sessions[thread_id] = {
        "history": [], "profile": {},
        "exchange_count": 0,
        "tokens": {"input": 0, "output": 0},
        "last_input_tokens": 0,
    }
    return jsonify({"thread_id": thread_id})

@app.route("/api/session/reset", methods=["POST"])
def reset_session():
    data = request.json or {}
    old_tid = data.get("thread_id", "")
    carried_profile = {}
    if old_tid and old_tid in sessions:
        carried_profile = sessions[old_tid].get("profile", {})
    new_tid = str(uuid.uuid4())
    sessions[new_tid] = {
        "history": [], "profile": dict(carried_profile),
        "exchange_count": 0,
        "tokens": {"input": 0, "output": 0}
    }
    return jsonify({"thread_id": new_tid, "carried_profile": carried_profile})

@app.route("/api/chat", methods=["POST"])
def chat():
    data      = request.json
    thread_id = data.get("thread_id")
    user_msg  = data.get("message", "").strip()

    if not thread_id or not user_msg:
        return jsonify({"error": "Missing thread_id or message"}), 400
    if thread_id not in sessions:
        sessions[thread_id] = {
            "history": [], "profile": {},
            "exchange_count": 0, "tokens": {"input": 0, "output": 0}
        }

    sess = sessions[thread_id]
    turn = sess["exchange_count"] + 1
    # Use last_input_tokens as proxy for context window fullness
    last_input = sess.get("last_input_tokens", 0)

    # Thread too long (context window getting very large, quality degrades)
    if last_input > 80000:
        return jsonify({
            "error": "Conversation trop longue, lance un nouveau parcours pour des reponses optimales.",
            "reset_url": "/api/session/reset"
        }), 413

    # Circuit breaker check
    ok, retry_in = _circuit_check()
    if not ok:
        return jsonify({
            "error": f"ORI est tres demande en ce moment (quota Vertex partage). Reessaie dans {retry_in}s.",
            "rate_limited": True,
            "retry_after": retry_in,
        }), 429

    # Enforce minimum 3s gap between queries to same thread (Vertex needs state flush)
    last_t = _last_query_time.get(thread_id, 0)
    elapsed = time.time() - last_t
    if elapsed < 3.0:
        time.sleep(3.0 - elapsed)

    t0 = time.time()
    reply, meta = None, {}
    last_err = None
    status = "OK"
    MAX_TOTAL_TIME = 50  # Hard cap: never exceed 50s total server-side

    # Retry logic: up to 4 attempts with backoff, capped at MAX_TOTAL_TIME
    max_attempts = 4
    for attempt in range(max_attempts):
        elapsed_total = time.time() - t0
        if elapsed_total > MAX_TOTAL_TIME:
            if not last_err:
                last_err = "Timeout total 50s"
            status = "TIMEOUT"
            break
        remaining = MAX_TOTAL_TIME - elapsed_total
        query_timeout = min(25, remaining - 1)  # leave 1s margin
        if query_timeout < 5:
            if not last_err:
                last_err = "Timeout total 50s"
            status = "TIMEOUT"
            break
        try:
            response = _query_with_timeout(thread_id, user_msg, timeout_s=int(query_timeout))
            reply, meta = parse_ori_response(response)
            _log_debug(thread_id, attempt + 1, response, reply, meta)

            # Detect LLM_ERROR responses (engine returns error as valid response)
            if reply and ("LLM_ERROR" in reply or reply.strip() in ("LLM_ERROR", "LLM_ERROR {}")):
                log.warning(f"[ORI] tid={thread_id[:8]} turn={turn} attempt={attempt+1} LLM_ERROR in response")
                reply = None
                if attempt < 3 and (time.time() - t0) < MAX_TOTAL_TIME - 8:
                    time.sleep(3 + attempt * 2)
                    continue
                last_err = "LLM_ERROR"
                status = "FAIL"
                break

            input_tok = meta.get("input_tokens_count", 0)
            output_tok = meta.get("output_tokens_count", 0)
            sess["tokens"]["input"]  += input_tok
            sess["tokens"]["output"] += output_tok
            sess["last_input_tokens"] = input_tok  # actual context window size
            _last_query_time[thread_id] = time.time()
            break

        except FuturesTimeout:
            last_err = "Timeout"
            status = "TIMEOUT"
            log.warning(f"[ORI] tid={thread_id[:8]} turn={turn} attempt={attempt+1} TIMEOUT")
            if attempt < 2 and (time.time() - t0) < MAX_TOTAL_TIME - 10:
                time.sleep(2)
                continue
            break

        except Exception as e:
            last_err = str(e)
            is_429 = any(kw in last_err for kw in ["429", "quota", "Quota", "RESOURCE_EXHAUSTED"])
            is_engine_busy = "Execution failed" in last_err or "400" in last_err[:10]
            is_5xx = (not is_engine_busy) and any(kw in last_err for kw in
                ["503", "502", "Service Unavailable", "overloaded", "temporarily"])

            if is_429:
                _circuit_record_429()
                status = "RATE_LIMITED"
                log.warning(f"[ORI] tid={thread_id[:8]} turn={turn} attempt={attempt+1} 429")
                if attempt < 1 and (time.time() - t0) < MAX_TOTAL_TIME - 12:
                    time.sleep(5)
                    continue
                break
            elif is_engine_busy:
                status = "RETRY"
                log.warning(f"[ORI] tid={thread_id[:8]} turn={turn} attempt={attempt+1} ENGINE_BUSY: {last_err[:80]}")
                if attempt < 3 and (time.time() - t0) < MAX_TOTAL_TIME - 8:
                    time.sleep(3 + attempt * 2)  # 3s, 5s, 7s
                    continue
                break
            elif is_5xx:
                status = "RETRY"
                log.warning(f"[ORI] tid={thread_id[:8]} turn={turn} attempt={attempt+1} 5xx: {last_err[:80]}")
                if attempt < 3 and (time.time() - t0) < MAX_TOTAL_TIME - 6:
                    time.sleep(2 + attempt)
                    continue
                break
            else:
                status = "FAIL"
                log.error(f"[ORI] tid={thread_id[:8]} turn={turn} attempt={attempt+1} ERROR: {last_err[:120]}")
                break

    latency_ms = int((time.time() - t0) * 1000)
    tok_cumul = sess["tokens"]["input"] + sess["tokens"]["output"]

    if reply is None:
        status = status if status != "OK" else "FAIL"
        log.info(f"[ORI] tid={thread_id[:8]} turn={turn} tok_cumul={tok_cumul} latency_ms={latency_ms} status={status}")
        err = last_err or "Unknown error"
        if "429" in err or "quota" in err.lower() or "RESOURCE_EXHAUSTED" in err:
            return jsonify({
                "error": "Limite de requetes atteinte. Reessaie dans 30 secondes.",
                "rate_limited": True,
                "retry_after": 30,
            }), 429
        if any(kw in err for kw in ["503","502","500","Unavailable","overloaded"]):
            return jsonify({"error": "ORI est indisponible en ce moment (Vertex AI surcharge). Reessaie dans 30 secondes."}), 503
        if "Timeout" in err:
            return jsonify({"error": "ORI met trop de temps a repondre. Reessaie dans quelques secondes."}), 504
        return jsonify({"error": "Erreur ORI : " + err[:200]}), 500

    log.info(f"[ORI] tid={thread_id[:8]} turn={turn} tok_cumul={tok_cumul} latency_ms={latency_ms} status={status}")

    sess["history"].append({"role": "user",      "content": user_msg})
    sess["history"].append({"role": "assistant", "content": reply})
    sess["exchange_count"] += 1
    _extract_profile(sess, user_msg + " " + reply)

    if _detect_bilan(reply):
        sess["last_bilan"] = reply

    has_bilan = _detect_bilan(reply)
    thread_warning = sess.get("last_input_tokens", 0) > 60000

    return jsonify({
        "reply":          reply,
        "exchange_count": sess["exchange_count"],
        "profile":        sess["profile"],
        "has_bilan":      has_bilan,
        "tokens":         sess["tokens"],
        "thread_id":      thread_id,
        "thread_warning": thread_warning,
    })

@app.route("/api/compare", methods=["POST"])
def compare():
    progs = request.json.get("programmes", [])
    return jsonify({p: PROGRAMMES[p] for p in progs if p in PROGRAMMES})

@app.route("/api/profile", methods=["GET"])
def get_profile():
    tid = request.args.get("thread_id")
    if tid and tid in sessions:
        return jsonify(sessions[tid]["profile"])
    return jsonify({})

@app.route("/api/handoff", methods=["GET"])
def handoff():
    return jsonify({
        "resources": [
            {"label": "Salons de l'Etudiant", "url": "https://www.letudiant.fr/salons.html", "desc": "Rencontre conseillers et ecoles en presentiel."},
            {"label": "PsyEN (Psychologue Education Nationale)", "url": "https://www.education.gouv.fr/les-psychologues-de-l-education-nationale-11512", "desc": "Gratuit, via ton lycee."},
            {"label": "Calendrier Parcoursup 2026", "url": "https://www.letudiant.fr/etudes/parcoursup.html", "desc": "Etapes officielles et dates cles."}
        ]
    })

@app.route("/api/bilan_pdf", methods=["GET"])
def bilan_pdf():
    from pdf_generator import generate_bilan_pdf
    tid = request.args.get("thread_id")
    if not tid or tid not in sessions:
        return jsonify({"error": "Session inconnue"}), 404
    sess = sessions[tid]
    if not sess["history"]:
        return jsonify({"error": "Conversation vide"}), 400
    pdf_bytes = generate_bilan_pdf(tid, sess["profile"], sess["history"], sess.get("last_bilan", ""))
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name=f"profil-orientation-ORI-{tid[:8]}.pdf")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"\n  ORI Companion — Groupe 1 — http://localhost:{port}")
    print(f"  Widget seul  → http://localhost:{port}/embed\n")
    app.run(debug=True, host="0.0.0.0", port=port)
