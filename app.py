from flask import Flask, render_template, request, jsonify, abort, Response, session, redirect, url_for
import os, json, datetime, csv, io
from io import StringIO
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import urllib.parse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# ---------------- APP ----------------
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
        type TEXT, customer TEXT, phone TEXT,
        model TEXT, problem TEXT,
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
        ink_name TEXT UNIQUE,
        created_at TEXT
    )
    """)

    # INK STOCK
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ink_stock(
        ink_id INTEGER UNIQUE,
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
        for i in inks:
            cur.execute(
                "INSERT INTO ink_master(ink_name,created_at) VALUES(%s,%s)",
                (i, now())
            )

    conn.commit()
    cur.close()
    conn.close()

init_db()

# ---------------- AUTH ----------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*a, **kw)
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
            session["role"] = u["role"]
            return redirect("/")
        return "Invalid login"
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ---------------- DASHBOARD ----------------
@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")

# ---------------- INK APIs ----------------
@app.get("/api/ink")
@login_required
def ink_list():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.ink_name, COALESCE(s.qty,0) qty
        FROM ink_master m
        LEFT JOIN ink_stock s ON m.id=s.ink_id
        ORDER BY m.ink_name
    """)
    data = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(data)

@app.post("/api/ink/in")
@login_required
def ink_in():
    d = request.get_json()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM ink_master WHERE ink_name=%s", (d["model"],))
    ink = cur.fetchone()
    if ink:
        cur.execute("""
            INSERT INTO ink_stock(ink_id,qty,updated_at)
            VALUES(%s,%s,%s)
            ON CONFLICT (ink_id)
            DO UPDATE SET qty=ink_stock.qty+%s, updated_at=%s
        """, (ink["id"], d["qty"], now(), d["qty"], now()))
    conn.commit()
    cur.close(); conn.close()
    return jsonify({"ok":True})

@app.post("/api/ink/sell")
@login_required
def ink_sell():
    d = request.get_json()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE ink_stock SET qty=qty-%s, updated_at=%s
        WHERE ink_id=(SELECT id FROM ink_master WHERE ink_name=%s)
    """, (d["qty"], now(), d["model"]))
    conn.commit()
    cur.close(); conn.close()
    return jsonify({"ok":True})

# ---------------- PRINT (PUBLIC) ----------------
@app.get("/print/<int:eid>")
def print_receipt(eid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id=%s", (eid,))
    r = cur.fetchone()
    cur.close(); conn.close()
    if not r:
        abort(404)
    return render_template("receipt.html", e=r)

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))