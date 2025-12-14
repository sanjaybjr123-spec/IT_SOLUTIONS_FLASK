from flask import Flask, render_template, request, jsonify, session, redirect
import os, datetime, json
import psycopg2, psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_db():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor
    )

# ---------------- AUTO DB MIGRATION ----------------
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
        status TEXT,
        out_date TEXT,
        in_date TEXT,
        ready_date TEXT,
        reject_date TEXT,
        delivered_date TEXT,
        amount REAL DEFAULT 0,
        bill_json TEXT
    )
    """)

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
        (type,customer,phone,model,problem,receive_date,status)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (
        d.get("type",""),
        d.get("customer",""),
        d.get("phone",""),
        d.get("model",""),
        d.get("problem",""),
        now(),
        "Received"
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
        "delivered": ("Delivered", "delivered_date"),
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

@app.delete("/api/entries/<int:eid>")
@login_required
def delete_entry(eid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM entries WHERE id=%s", (eid,))
    conn.commit()
    cur.close(); conn.close()
    return jsonify(ok=True)

@app.post("/api/entries/<int:eid>/bill")
@login_required
def save_bill(eid):
    d = request.get_json(force=True)
    total = float(d.get("parts_total",0)) + float(d.get("service_charge",0)) + float(d.get("other",0))
    bill = {**d, "total": total}

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE entries SET amount=%s, bill_json=%s WHERE id=%s",
        (total, json.dumps(bill), eid)
    )
    conn.commit()
    cur.close(); conn.close()
    return jsonify(ok=True)

@app.route("/print/<int:eid>")
@login_required
def print_receipt(eid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id=%s", (eid,))
    r = cur.fetchone()
    cur.close(); conn.close()

    bill = json.loads(r["bill_json"]) if r.get("bill_json") else {}
    return render_template("receipt.html", e=r, bill=bill)

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))