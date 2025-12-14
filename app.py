from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for
import os, json, datetime, csv
from io import StringIO
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

# ---------------- APP ----------------

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------- DATABASE ----------------

def get_db():
    DATABASE_URL = os.environ.get("DATABASE_URL")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor
    )

# ---------------- INIT DB ----------------

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS entries(
            id SERIAL PRIMARY KEY,
            type TEXT, customer TEXT, phone TEXT, model TEXT, problem TEXT,
            receive_date TEXT,
            out_date TEXT,
            in_date TEXT,
            ready_date TEXT,
            return_date TEXT,
            reject_date TEXT,
            status TEXT,
            bill_json TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS sales(
            id SERIAL PRIMARY KEY,
            sale_date TEXT, item TEXT, qty REAL, rate REAL,
            amount REAL, payment_mode TEXT, note TEXT
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
    except Exception as e:
        print("DB init skipped:", e)
    finally:
        try:
            cur.close()
            conn.close()
        except:
            pass

init_db()

# ---------------- AUTH ----------------

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            return jsonify({"error": "Admin only"}), 403
        return fn(*args, **kwargs)
    return wrapper

# ---------------- LOGIN ----------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username")
        p = request.form.get("password")

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s", (u,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user["password_hash"], p):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------------- HELPERS ----------------

def row_to_obj(r):
    return {
        "id": r["id"],
        "type": r["type"],
        "customer": r["customer"],
        "phone": r["phone"],
        "model": r["model"],
        "problem": r["problem"],
        "receive_date": r["receive_date"],
        "out_date": r["out_date"],
        "in_date": r["in_date"],
        "ready_date": r["ready_date"],
        "return_date": r["return_date"],
        "reject_date": r["reject_date"],
        "status": r["status"],
        "bill": json.loads(r["bill_json"]) if r["bill_json"] else {}
    }

# ---------------- DASHBOARD ----------------

@app.route("/")
@login_required
def dashboard():
    conn = get_db()
    cur = conn.cursor()

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    cur.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM sales WHERE sale_date LIKE %s",
        (today + "%",)
    )
    today_sales = cur.fetchone()["s"]

    cur.execute("SELECT COUNT(*) n FROM entries WHERE status!='Delivered'")
    pending = cur.fetchone()["n"]

    # OVERDUE (7 days rule)
    cur.execute("""
        SELECT COUNT(*) n FROM entries
        WHERE status IN ('Received','Out','In')
        AND receive_date < %s
    """, ((datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d"),))
    overdue = cur.fetchone()["n"]

    cur.close()
    conn.close()

    return render_template("dashboard.html", kp={
        "today_sales": today_sales,
        "pending": pending,
        "overdue": overdue,
        "ledger_bal": 0
    })

# ---------------- SERVICE ----------------

@app.get("/api/entries")
@login_required
def list_entries():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([row_to_obj(r) for r in rows])

@app.post("/api/entries")
@login_required
def add_entry():
    d = request.get_json(force=True)
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO entries(type,customer,phone,model,problem,receive_date,status)
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
    cur.close()
    conn.close()
    return jsonify({"ok": True})

# ---------------- ENTRY ACTIONS ----------------

@app.post("/api/entries/<int:eid>/action")
@login_required
def entry_action(eid):
    d = request.get_json(force=True)
    a = d.get("action")
    t = now()

    conn = get_db()
    cur = conn.cursor()

    mapping = {
        "out": ("Out", "out_date"),
        "in": ("In", "in_date"),
        "ready": ("Ready", "ready_date"),
        "delivered": ("Delivered", "return_date"),
        "reject": ("Rejected", "reject_date")
    }

    if a not in mapping:
        return jsonify({"error": "Invalid action"}), 400

    status, field = mapping[a]
    cur.execute(f"UPDATE entries SET status=%s, {field}=%s WHERE id=%s",
                (status, t, eid))

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})

# ---------------- DELETE ----------------

@app.delete("/api/entries/<int:eid>")
@login_required
@admin_required
def delete_entry(eid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM entries WHERE id=%s", (eid,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"deleted": True})

# ---------------- EXPORT ----------------

def export_csv(query, filename):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    si = StringIO()
    writer = csv.writer(si)

    if rows:
        writer.writerow(rows[0].keys())
        for r in rows:
            writer.writerow(r.values())

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/export/entries")
@login_required
@admin_required
def export_entries():
    return export_csv("SELECT * FROM entries ORDER BY id DESC", "entries_backup.csv")

# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))