from flask import Flask, render_template, request, jsonify, abort, Response, session, redirect, url_for
import os, json, datetime, csv
from io import StringIO
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import urllib.parse

# ---------------- APP ----------------
app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")

# ---------------- HELPERS ----------------
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

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # USERS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE,
        password_hash TEXT,
        role TEXT
    )
    """)

    # ENTRIES
    cur.execute("""
    CREATE TABLE IF NOT EXISTS entries(
        id SERIAL PRIMARY KEY,
        type TEXT,
        customer TEXT,
        phone TEXT,
        model TEXT,
        problem TEXT,
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

    # SALES
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales(
        id SERIAL PRIMARY KEY,
        sale_date TEXT,
        item TEXT,
        qty REAL,
        rate REAL,
        amount REAL,
        payment_mode TEXT,
        note TEXT
    )
    """)

    # INK MASTER
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ink_master(
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE,
        created_at TEXT
    )
    """)

    # INK STOCK
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ink_stock(
        ink_id INTEGER PRIMARY KEY,
        qty INTEGER,
        updated_at TEXT
    )
    """)

    # DEFAULT ADMIN
    cur.execute("SELECT COUNT(*) c FROM users")
    if cur.fetchone()["c"] == 0:
        cur.execute(
            "INSERT INTO users(username,password_hash,role) VALUES(%s,%s,%s)",
            ("admin", generate_password_hash("admin@123"), "admin")
        )

    # DEFAULT INKS
    cur.execute("SELECT COUNT(*) c FROM ink_master")
    if cur.fetchone()["c"] == 0:
        inks = [
            "HP 680 Black","HP 680 Color",
            "Canon 790 Black","Canon 790 Color",
            "Epson 003 Black","Epson 003 Color"
        ]
        for ink in inks:
            cur.execute(
                "INSERT INTO ink_master(name,created_at) VALUES(%s,%s)",
                (ink, now())
            )
            cur.execute(
                "INSERT INTO ink_stock(ink_id,qty,updated_at) VALUES(currval('ink_master_id_seq'),0,%s)",
                (now(),)
            )

    conn.commit()
    cur.close()
    conn.close()

init_db()

# ---------------- AUTH ----------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET","POST"])
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

# ---------------- DASHBOARD ----------------
@app.route("/")
@login_required
def dashboard():
    conn = get_db()
    cur = conn.cursor()

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    cur.execute("SELECT COALESCE(SUM(amount),0) s FROM sales WHERE sale_date LIKE %s", (today+"%",))
    today_sales = cur.fetchone()["s"]

    cur.execute("SELECT COUNT(*) n FROM entries WHERE status!='Delivered'")
    pending = cur.fetchone()["n"]

    ten = (datetime.datetime.now()-datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    cur.execute("SELECT COUNT(*) n FROM entries WHERE status!='Delivered' AND receive_date < %s", (ten,))
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
@app.route("/service")
@login_required
def service_page():
    return render_template("service.html")

@app.get("/api/entries")
@login_required
def list_entries():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)

@app.post("/api/entries")
@login_required
def add_entry():
    d = request.get_json()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO entries(type,customer,phone,model,problem,receive_date,status)
        VALUES(%s,%s,%s,%s,%s,%s,%s)
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
    return jsonify(ok=True)

# ---------------- INK STOCK ----------------
@app.get("/api/ink")
@login_required
def ink_list():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.id, m.name, COALESCE(s.qty,0) qty
        FROM ink_master m
        LEFT JOIN ink_stock s ON m.id=s.ink_id
        ORDER BY m.name
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)

@app.post("/api/ink/in")
@login_required
def ink_in():
    d = request.get_json()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE ink_stock
        SET qty = qty + %s, updated_at=%s
        WHERE ink_id=%s
    """, (int(d["qty"]), now(), int(d["id"])))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify(ok=True)

@app.post("/api/ink/sell")
@login_required
def ink_sell():
    d = request.get_json()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE ink_stock
        SET qty = qty - %s, updated_at=%s
        WHERE ink_id=%s AND qty >= %s
    """, (int(d["qty"]), now(), int(d["id"]), int(d["qty"])))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify(ok=True)

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))