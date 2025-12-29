from flask import Flask, render_template, request, jsonify, abort, Response, session, redirect, url_for
import os, json, datetime, csv
from io import StringIO
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import urllib.parse

# ================= APP =================
app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ================= AUTH HELPERS =================
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

# ================= DATABASE =================   
def get_db():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")

    return psycopg2.connect(
        db_url,
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor
    )

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()

        # ---- USERS ----
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            password_hash TEXT,
            role TEXT
        )
        """)

        # ---- ENTRIES ----
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

        # ---- SALES ----
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

        # ---- INK MASTER ----
        cur.execute("""
        CREATE TABLE IF NOT EXISTS ink_master(
            id SERIAL PRIMARY KEY,
            ink_name TEXT UNIQUE
        )
        """)

        # ---- INK STOCK ----
        cur.execute("""
        CREATE TABLE IF NOT EXISTS ink_stock(
            ink_id INTEGER PRIMARY KEY,
            qty INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """)

        # ✅ ---- INK TRANSACTIONS (VERY IMPORTANT) ----
        cur.execute("""
        CREATE TABLE IF NOT EXISTS ink_transactions(
            id SERIAL PRIMARY KEY,
            ink_id INTEGER,
            ink_name TEXT,
            qty INTEGER,
            action TEXT,
            action_date TEXT
        )
        """)

        # ---- DEFAULT ADMIN ----
        cur.execute("SELECT COUNT(*) c FROM users")
        if cur.fetchone()["c"] == 0:
            cur.execute(
                "INSERT INTO users(username,password_hash,role) VALUES(%s,%s,%s)",
                ("admin", generate_password_hash("admin@123"), "admin")
            )

        conn.commit()
        cur.close()
        conn.close()

        print("DB init done")

    except Exception as e:
        print("DB init skipped:", e)

init_db()
        
        
        
               

# ================= LOGIN =================
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

        return render_template("login.html", error="Invalid login")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ================= HELPERS =================
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

def whatsapp_link(entry, total):
    msg = f"IT SOLUTIONS\nModel: {entry['model']}\nTotal: ₹{total}"
    return f"https://wa.me/91{entry['phone']}?text={urllib.parse.quote(msg)}"

# ================= DASHBOARD =================
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

    ten_days_ago = (datetime.datetime.now()-datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    cur.execute("""
        SELECT COUNT(*) n FROM entries
        WHERE status!='Delivered' AND receive_date < %s
    """, (ten_days_ago,))
    overdue = cur.fetchone()["n"]

    cur.close()
    conn.close()

    return render_template("dashboard.html", kp={
        "today_sales": today_sales,
        "pending": pending,
        "overdue": overdue,
        "ledger_bal": 0
    })

# ================= SERVICE =================
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

    out = []
    for r in rows:
        obj = row_to_obj(r)
        b = obj["bill"]
        total = b.get("parts_total",0)+b.get("service_charge",0)+b.get("other",0)
        obj["whatsapp"] = whatsapp_link(obj,total) if obj["status"]=="Delivered" else ""
        out.append(obj)

    return jsonify(out)

@app.post("/api/entries")
@login_required
def add_entry():
    d = request.get_json(force=True)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO entries(type,customer,phone,model,problem,receive_date,status)
        VALUES(%s,%s,%s,%s,%s,%s,%s)
    """, (
        d.get("type",""), d.get("customer",""), d.get("phone",""),
        d.get("model",""), d.get("problem",""), now(), "Received"
    ))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok":True})

# ================= ENTRY ACTION =================
@app.post("/api/entries/<int:eid>/action")
@login_required
def entry_action(eid):
    d = request.get_json(force=True)
    t = now()
    m = {
        "out":("Out","out_date"),
        "in":("In","in_date"),
        "ready":("Ready","ready_date"),
        "delivered":("Delivered","return_date"),
        "reject":("Rejected","reject_date")
    }
    if d["action"] not in m:
        return jsonify({"error":"invalid"}),400

    status,col = m[d["action"]]
    conn=get_db();cur=conn.cursor()
    cur.execute(f"UPDATE entries SET status=%s,{col}=%s WHERE id=%s",(status,t,eid))
    conn.commit();cur.close();conn.close()
    return jsonify({"ok":True})

# ================= BILL =================
@app.post("/api/entries/<int:eid>/bill")
@login_required
def save_bill(eid):
    d=request.get_json(force=True)
    bill={
        "parts_total":float(d.get("parts_total") or 0),
        "service_charge":float(d.get("service_charge") or 0),
        "other":float(d.get("other") or 0)
    }
    total=sum(bill.values())

    conn=get_db();cur=conn.cursor()
    cur.execute("UPDATE entries SET bill_json=%s WHERE id=%s",(json.dumps(bill),eid))
    cur.execute("""
        INSERT INTO sales(sale_date,item,qty,rate,amount,payment_mode,note)
        VALUES(%s,%s,1,%s,%s,%s,%s)
    """,(now(),"Service",total,total,"Cash",f"Entry {eid}"))
    conn.commit();cur.close();conn.close()
    return jsonify({"ok":True})

# ================= OVERDUE =================
@app.route("/overdue")
@login_required
def overdue_page():
    return render_template("overdue.html")

@app.get("/api/overdue")
@login_required
def overdue_list():
    conn=get_db();cur=conn.cursor()
    d=(datetime.datetime.now()-datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    cur.execute("SELECT * FROM entries WHERE status!='Delivered' AND receive_date<%s",(d,))
    r=cur.fetchall();cur.close();conn.close()
    return jsonify([row_to_obj(x) for x in r])

# ================= EXPORT =================
@app.get("/export/entries")
@login_required
def export_entries():
    conn=get_db();cur=conn.cursor()
    cur.execute("SELECT * FROM entries");rows=cur.fetchall()
    cur.close();conn.close()
    si=StringIO();cw=csv.writer(si)
    if rows:
        cw.writerow(rows[0].keys())
        for r in rows:cw.writerow(r.values())
    return Response(si.getvalue(),mimetype="text/csv",
        headers={"Content-Disposition":"attachment;filename=entries.csv"})
# ---------- EXPORT INK HISTORY ----------
@app.get("/export/ink")
@login_required
def export_ink_history():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT action_date, ink_name, qty, action
        FROM ink_transactions
        ORDER BY action_date ASC
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)

    cw.writerow(["Date", "Ink Name", "Quantity", "Type"])

    for r in rows:
        cw.writerow([
            r["action_date"],
            r["ink_name"],
            r["qty"],
            r["action"]
        ])

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment;filename=ink_history.csv"
        }
    )
# ================= INK STOCK =================
@app.route("/ink")
@login_required
def ink_page():
    return render_template("ink.html")

@app.get("/api/ink")
@login_required
def ink_list():
    conn = get_db()
    cur = conn.cursor()

    # tables ensure (safe)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ink_master(
            id SERIAL PRIMARY KEY,
            ink_name TEXT UNIQUE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ink_stock(
            ink_id INTEGER PRIMARY KEY,
            qty INTEGER DEFAULT 0,
            updated_at TEXT
        )
    """)

    # 🔧 FIXED COLUMN NAME
    cur.execute("""
        SELECT m.id,
               m.ink_name AS model,
               COALESCE(s.qty,0) AS qty
        FROM ink_master m
        LEFT JOIN ink_stock s ON m.id = s.ink_id
        ORDER BY m.ink_name
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)
        
@app.post("/api/ink/in")
@login_required
def ink_in():
    d = request.get_json(force=True)

    conn = get_db()
    cur = conn.cursor()

    # update stock
    cur.execute("""
        INSERT INTO ink_stock(ink_id,qty,updated_at)
        VALUES(%s,%s,%s)
        ON CONFLICT (ink_id)
        DO UPDATE SET qty=ink_stock.qty+%s, updated_at=%s
    """, (d["id"], d["qty"], now(), d["qty"], now()))

    # save IN history
    cur.execute("""
        INSERT INTO ink_transactions
        (ink_id, ink_name, qty, action, action_date)
        SELECT id, ink_name, %s, 'IN', %s
        FROM ink_master WHERE id=%s
    """, (d["qty"], action_date, d["id"]))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"ok": True})


@app.post("/api/ink/sell")
@login_required
def ink_sell():
    d = request.get_json(force=True)

    conn = get_db()
    cur = conn.cursor()

    # reduce stock
    cur.execute("""
        UPDATE ink_stock
        SET qty = qty - %s, updated_at = %s
        WHERE ink_id = %s AND qty >= %s
    """, (d["qty"], now(), d["id"], d["qty"]))

    # save SELL history
    cur.execute("""
        INSERT INTO ink_transactions
        (ink_id, ink_name, qty, action, action_date)
        SELECT id, ink_name, %s, 'SELL', %s
        FROM ink_master WHERE id=%s
    """, (d["qty"], action_date, d["id"]))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"ok": True})

# ---------- ADD NEW INK MODEL ----------
@app.post("/api/ink/model")
@login_required
def add_ink_model():
    d = request.get_json(force=True)
    name = d.get("model","").strip()

    if not name:
        return jsonify({"error":"Ink name required"}),400

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO ink_master(ink_name)
        VALUES(%s)
        ON CONFLICT DO NOTHING
    """, (name,))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"ok": True})

# ---------- DELETE INK MODEL ----------
@app.delete("/api/ink/<int:ink_id>")
@login_required
def delete_ink(ink_id):
    conn = get_db()
    cur = conn.cursor()

    # पहले stock delete
    cur.execute("DELETE FROM ink_stock WHERE ink_id=%s", (ink_id,))
    # फिर master delete
    cur.execute("DELETE FROM ink_master WHERE id=%s", (ink_id,))

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

# ---------------- PRINT ----------------
@app.get("/print/<int:eid>")
def print_receipt(eid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id=%s", (eid,))
    r = cur.fetchone()
    cur.close()
    conn.close()
    if not r:
        abort(404)

    return render_template("receipt.html", e=row_to_obj(r),
        shop={"name":"IT SOLUTIONS","addr":"GHATSILA COLLEGE ROAD"}
    )


# ================= RUN =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))