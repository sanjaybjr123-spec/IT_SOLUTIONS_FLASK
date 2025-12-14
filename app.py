from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os, datetime
import psycopg2, psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------- DATABASE ----------------
def get_db():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor
    )

# ---------------- AUTO DB FIX (NO SQL NEEDED) ----------------
def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS entries(
        id SERIAL PRIMARY KEY,
        type TEXT,
        customer TEXT,
        phone TEXT,
        model TEXT,
        problem TEXT,
        receive_date TEXT,
        status TEXT
    )
    """)

    # 🔥 AUTO ADD MISSING COLUMNS
    auto_cols = [
        "ALTER TABLE entries ADD COLUMN IF NOT EXISTS out_date TEXT",
        "ALTER TABLE entries ADD COLUMN IF NOT EXISTS in_date TEXT",
        "ALTER TABLE entries ADD COLUMN IF NOT EXISTS ready_date TEXT",
        "ALTER TABLE entries ADD COLUMN IF NOT EXISTS reject_date TEXT",
        "ALTER TABLE entries ADD COLUMN IF NOT EXISTS delivered_date TEXT",
        "ALTER TABLE entries ADD COLUMN IF NOT EXISTS amount REAL DEFAULT 0"
    ]

    for q in auto_cols:
        try:
            cur.execute(q)
        except:
            pass

    # users table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE,
        password_hash TEXT,
        role TEXT
    )
    """)

    cur.execute("SELECT COUNT(*) c FROM users")
    if cur.fetchone()["c"] == 0:
        cur.execute(
            "INSERT INTO users(username,password_hash,role) VALUES(%s,%s,%s)",
            ("admin", generate_password_hash("admin@123"), "admin")
        )

    conn.commit()
    cur.close()
    conn.close()

init_db()

# ---------------- AUTH ----------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if "user_id" not in session:
            return redirect("/login")
        return fn(*a, **k)
    return wrapper

# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s", (request.form["username"],))
        u = cur.fetchone()
        cur.close(); conn.close()

        if u and check_password_hash(u["password_hash"], request.form["password"]):
            session["user_id"] = u["id"]
            return redirect("/")
        return render_template("login.html", error="Invalid Login")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ---------------- PAGES ----------------
@app.route("/")
@login_required
def dashboard():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE status!='Delivered'")
    rows = cur.fetchall()
    cur.close(); conn.close()

    overdue = 0
    now_dt = datetime.datetime.now()
    for r in rows:
        try:
            if (now_dt - datetime.datetime.strptime(r["receive_date"], "%Y-%m-%d %H:%M:%S")).days > 10:
                overdue += 1
        except:
            pass

    return render_template("dashboard.html", kp={
        "today_sales": 0,
        "pending": len(rows),
        "overdue": overdue,
        "ledger_bal": 0
    })

@app.route("/service")
@login_required
def service_page():
    return render_template("service.html")

@app.route("/overdue")
@login_required
def overdue_page():
    return render_template("overdue.html")

# ---------------- API ----------------
@app.post("/api/entries")
@login_required
def add_entry():
    d = request.get_json(force=True)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO entries
        (type,customer,phone,model,problem,receive_date,status,amount)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        d.get("type",""),
        d.get("customer",""),
        d.get("phone",""),
        d.get("model",""),
        d.get("problem",""),
        now(),
        "Received",
        float(d.get("amount",0))
    ))
    conn.commit()
    cur.close(); conn.close()
    return jsonify(ok=True)

@app.get("/api/entries")
@login_required
def api_entries():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

# ✅ DELIVERED FIXED
@app.post("/api/entries/<int:eid>/action")
@login_required
def entry_action(eid):
    action = request.get_json(force=True).get("action","").lower()
    t = now()

    mapping = {
        "out": ("Out", "out_date"),
        "in": ("In", "in_date"),
        "ready": ("Ready", "ready_date"),
        "reject": ("Rejected", "reject_date"),
        "delivered": ("Delivered", "delivered_date")
    }

    if action not in mapping:
        return jsonify(error="Invalid action"), 400

    status, field = mapping[action]

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE entries SET status=%s, {field}=%s WHERE id=%s",
        (status, t, eid)
    )
    conn.commit()
    cur.close(); conn.close()
    return jsonify(ok=True)

# ---------------- PRINT (FIXED) ----------------
@app.route("/print/<int:eid>")
@login_required
def print_receipt(eid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT *,
        COALESCE(amount,0) AS amount
        FROM entries WHERE id=%s
    """, (eid,))
    r = cur.fetchone()
    cur.close(); conn.close()

    if not r:
        return "Not found", 404

    return render_template("receipt.html", e=r)

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))