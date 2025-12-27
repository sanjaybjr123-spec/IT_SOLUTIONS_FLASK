from flask import (
    Flask, render_template, request, jsonify,
    abort, Response, session, redirect, url_for
)
import os, json, datetime, csv
from io import StringIO
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import urllib.parse
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

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

def init_db():
    try:
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
            sale_date TEXT,
            item TEXT,
            qty REAL,
            rate REAL,
            amount REAL,
            payment_mode TEXT,
            note TEXT
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
    except Exception as e:
        print("DB init skipped:", e)

init_db()

# ---------------- AUTH ----------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if session.get("role") != "admin":
            abort(403)
        return fn(*a, **kw)
    return wrapper

# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = request.form["username"]
        p = request.form["password"]

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

        return render_template("login.html", error="Invalid credentials")

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
        "status": r["status"],
        "bill": json.loads(r["bill_json"] or "{}")
    }

def whatsapp_pdf_link(entry):
    pdf = f"https://it-solutions-flask.onrender.com/print/{entry['id']}"
    msg = f"Your bill is ready:\n{pdf}"
    return f"https://wa.me/91{entry['phone']}?text={urllib.parse.quote(msg)}"

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
    cur.execute("SELECT COUNT(*) n FROM entries WHERE status!='Delivered' AND receive_date<%s",(ten,))
    overdue = cur.fetchone()["n"]

    cur.close(); conn.close()

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
    cur.close(); conn.close()

    out=[]
    for r in rows:
        o=row_to_obj(r)
        if o["status"]=="Delivered":
            o["whatsapp"]=whatsapp_pdf_link(o)
        out.append(o)
    return jsonify(out)

@app.post("/api/entries")
@login_required
def add_entry():
    d=request.get_json()
    conn=get_db(); cur=conn.cursor()
    cur.execute("""
        INSERT INTO entries(type,customer,phone,model,problem,receive_date,status)
        VALUES(%s,%s,%s,%s,%s,%s,%s)
    """,(d["type"],d["customer"],d["phone"],d["model"],d["problem"],now(),"Received"))
    conn.commit()
    cur.close(); conn.close()
    return jsonify(ok=True)

# ---------------- ACTIONS ----------------
@app.post("/api/entries/<int:eid>/action")
@login_required
def entry_action(eid):
    a=request.get_json()["action"]
    conn=get_db(); cur=conn.cursor()
    cur.execute("UPDATE entries SET status=%s WHERE id=%s",(a,eid))
    conn.commit()
    cur.close(); conn.close()
    return jsonify(ok=True)

# ---------------- BILL ----------------
@app.post("/api/entries/<int:eid>/bill")
@login_required
def save_bill(eid):
    d=request.get_json()
    total=float(d["parts_total"])+float(d["service_charge"])+float(d["other"])
    conn=get_db(); cur=conn.cursor()
    cur.execute("UPDATE entries SET bill_json=%s WHERE id=%s",(json.dumps(d),eid))
    cur.execute(
        "INSERT INTO sales VALUES(DEFAULT,%s,'Service',1,%s,%s,%s,'')",
        (now(),total,total,d["payment_mode"])
    )
    conn.commit()
    cur.close(); conn.close()
    return jsonify(ok=True)

# ---------------- PRINT ----------------
@app.get("/print/<int:eid>")
def print_receipt(eid):
    conn=get_db(); cur=conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id=%s",(eid,))
    r=cur.fetchone()
    cur.close(); conn.close()
    if not r: abort(404)
    return render_template("receipt.html", e=row_to_obj(r),
        shop={"name":"IT SOLUTIONS","addr":"GHATSILA COLLEGE ROAD"}
    )

# ---------------- RUN ----------------
if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))