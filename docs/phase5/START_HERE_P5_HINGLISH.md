# Phase 5 (web app): tumhare liye quick start

Ye kit **Phase 5 (P5-1 se P5-7)** ka poora web app hai. Isme sirf `app/`, `tools/p5_*` aur `docs/phase5/` files hain. Tumhari baaki files (db/, docs/design/, app/security.py, tools/schema_spec.py ...) ko ye **overwrite nahi karta**.

## Sabse pehle: kya test hua, kya nahi (honest)
| Check | Status |
|---|---|
| Saari Python files syntax + undefined-name check | PASS (`tools/p5_static_check.py`) |
| 53 HTML templates render (khaali aur bhare dono case) | PASS (`tools/p5_smoke_templates.py`) |
| DB layer ka procedure-argument matching (fake connection par) | PASS (`tools/p5_selftest_db.py`) |
| **Asli MySQL ke saath chalna** | **NAHI hua** (mere sandbox me MySQL/FastAPI install nahi tha, internet bhi nahi) |
| FastAPI server start hona | **NAHI hua**, tumhare machine par pehli baar hoga |

Matlab: code ki structure aur logic solid hai, lekin **pehli real run mein chhoti errors aayengi** (column ka naam, procedure ka argument naam). Ye normal hai. Ek tool hai jo ye sab ek saath dikha deta hai (neeche Step 6).

## Step 1: kit ko repo me daalo (Git Bash, repo ke root me)
```bash
# zip ko clinic-dbms folder me copy karke:
unzip -o phase5-web-kit.zip
git status          # app/, tools/p5_*, docs/phase5/ naye dikhne chahiye
```
Agar tumhare paas pehle se `app/security.py` hai to wo waisi hi rahegi.

## Step 2: .env me ye lines jodo (file `.env`, repo root)
```bash
python -c "import secrets;print('APP_SECRET_KEY='+secrets.token_hex(32))" >> .env
cat docs/phase5/env.phase5.example >> .env      # phir .env kholkar dekh lo
```
`.gitignore` me `uploads/` bhi add karo (lab report files yahan save hoti hain):
```bash
echo "uploads/" >> .gitignore
```

## Step 3: Python environment
```bash
python -m venv .venv
source .venv/Scripts/activate        # Windows Git Bash.  (Mac/Linux: source .venv/bin/activate)
pip install -r app/requirements.txt
```

## Step 4: bina database ke checks (30 second)
```bash
python tools/p5_static_check.py
python tools/p5_smoke_templates.py
python tools/p5_selftest_db.py
```
Teeno ke end me "0 problem(s)" / "0 failure(s)" / sab PASS dikhna chahiye.

## Step 5: STUB mode: bina database ke saari screens dekho
```bash
DB_MODE=stub uvicorn app.main:app --reload --port 8000
```
Browser: http://localhost:8000 → login `owner` / `doctor` / `reception` / `nurse` / `pharmacy` / `accountant`, password `demo`.
Screens khaali dikhengi (data nahi hai) aur kuch save nahi hoga. Ye layout/menu/role checks ke liye hai. (Owner ke 2 roles hain, to "choose role" screen bhi dikhegi.)

## Step 6: ASLI mode (database ke saath)
```bash
docker compose up -d
bash tools/run_sql_checked.sh                 # original agent ka tool: schema + logic load
# pehla owner banao: original agent ka tools/bootstrap_owner.py (uska README dekho)
uvicorn app.main:app --reload --port 8000
```
Phir **ye sabse important command**:
```bash
python tools/p5_check_contract.py > p5_contract_report.txt
```
Ye batata hai: kaunsa stored procedure DB me nahi hai, kiska argument naam alag hai, kaunsa table-column mismatch hai. **Is file ko original agent ko do** (guide ke saath).
Browser me bhi dekh sakte ho: owner login → menu "Diagnostics" (`/_diag`).

## Step 7: Original agent ko kya dena hai (copy-paste)
1. `docs/phase5/PHASE5_HANDOFF_GUIDE.md` (ye mera poora guide, usi ke liye likha hai)
2. `docs/phase5/DB_CONTRACT.md` (mujhe DB se kya chahiye, procedure-by-procedure)
3. `p5_contract_report.txt` (Step 6 ka output)
4. Browser ya terminal me aayi koi bhi error ka exact text

Aur bolo: *"Phase 5 web app integrate karo. Guide section 9 ka prompt follow karo."* (prompt guide ke andar ready hai.)

## Step 8: commit
```bash
git add app tools docs/phase5 .gitignore
git commit -m "P5: web app (reception, doctor, pharmacy, nurse, billing, owner, family)"
git push
```

## Agar kuch toote
- `ModuleNotFoundError` → venv activate nahi hai, Step 3 dobara.
- Login "Wrong login or password" → `staff_credential` me password hash nahi hai; owner create karne wale tool ya `/owner/staff` se set karo (guide A-01, A-02).
- Screen par "needs a database object that is missing" → ye expected hai jab tak us task (P3-3..P3-6) ki procedure bani nahi; `/_diag` me exact naam dikhta hai.
