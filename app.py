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

        cur.execute("SELECT COUNT(*) AS c FROM users")
        if cur.fetchone()["c"] == 0:
            cur.execute("""
                INSERT INTO users(username, password_hash, role)
                VALUES (%s,%s,%s)
            """, ("admin", generate_password_hash("admin@123"), "admin"))

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("DB init skipped:", e)
# ---------- INK MASTER ----------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS ink_master(
            id SERIAL PRIMARY KEY,
            model TEXT UNIQUE
        )
        """)

        # ---------- INK STOCK ----------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS ink_stock(
            ink_id INTEGER PRIMARY KEY,
            qty INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """)

        # ---------- DEFAULT INK DATA ----------
        cur.execute("SELECT COUNT(*) c FROM ink_master")
        if cur.fetchone()["c"] == 0:
            inks = [
                "HP 680 Black",
                "HP 680 Color",
                "Canon 790 Black",
                "Canon 790 Color",
                "Epson 003 Black",
                "Epson 003 Color"
            ]
            for i in inks:
                cur.execute(
                    "INSERT INTO ink_master(model) VALUES (%s)",
                    (i,)
                )

init_db()
# ---------------- INK STOCK PAGES ----------------

@app.route("/ink")
@login_required
def ink_page():
    return render_template("ink.html")


@app.get("/api/ink")
@login_required
def ink_list():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT m.id, m.model, COALESCE(s.qty,0) qty
        FROM ink_master m
        LEFT JOIN ink_stock s ON m.id = s.ink_id
        ORDER BY m.model
    """)
    rows = cur.fetchall()

    cur.close()
    conn.close()
    return jsonify(rows)


@app.post("/api/ink/in")
@login_required
def ink_in():
    d = request.get_json(force=True)
    ink_id = int(d.get("id"))
    qty = int(d.get("qty"))

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO ink_stock(ink_id, qty, updated_at)
        VALUES(%s,%s,%s)
        ON CONFLICT (ink_id)
        DO UPDATE SET qty=ink_stock.qty+%s, updated_at=%s
    """, (ink_id, qty, now(), qty, now()))

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/ink/sell")
@login_required
def ink_sell():
    d = request.get_json(force=True)
    ink_id = int(d.get("id"))
    qty = int(d.get("qty"))

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE ink_stock
        SET qty = qty - %s, updated_at = %s
        WHERE ink_id = %s AND qty >= %s
    """, (qty, now(), ink_id, qty))

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})

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

def whatsapp_bill_link(entry, total):
    msg = f"""
🧾 IT SOLUTIONS

Customer: {entry['customer']}
Model: {entry['model']}

💰 Total Bill: ₹{total}

🙏 Thank you!
"""
    text = urllib.parse.quote(msg)
    phone = entry["phone"].replace("+","").replace(" ","")
    return f"https://wa.me/91{phone}?text={text}"

# ---------------- EXPORT ----------------
@app.get("/export/entries")
@login_required
def export_entries():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)

    if rows:
        cw.writerow(rows[0].keys())
        for r in rows:
            cw.writerow(r.values())

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=entries.csv"}
    )

# ---------------- DASHBOARD ----------------
@app.route("/")
@login_required
def dashboard():
    conn = get_db()
    cur = conn.cursor()

    today = datetime.datetime.now().strftime("%Y-%m-%d")

    # Today sales
    cur.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM sales WHERE sale_date LIKE %s",
        (today+"%",)
    )
    today_sales = cur.fetchone()["s"]

    # Pending
    cur.execute("SELECT COUNT(*) n FROM entries WHERE status!='Delivered'")
    pending = cur.fetchone()["n"]

    # Overdue (>10 days)
    ten_days_ago = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    cur.execute("""
        SELECT COUNT(*) n FROM entries
        WHERE status!='Delivered'
        AND receive_date < %s
    """, (ten_days_ago,))
    overdue = cur.fetchone()["n"]

    cur.close()
    conn.close()

    return render_template("dashboard.html", kp={
        "today_sales": today_sales,
        "pending": pending,
        "overdue": overdue,   # ✅ ab sahi count
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

    data = []
    for r in rows:
        obj = row_to_obj(r)

        bill = obj["bill"]
        total = bill.get("parts_total",0) + bill.get("service_charge",0) + bill.get("other",0)

        obj["whatsapp"] = whatsapp_bill_link(obj, total) if obj["status"]=="Delivered" and obj["phone"] else ""
        data.append(obj)

    return jsonify(data)

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

# ---------------- OVERDUE ----------------
@app.route("/overdue")
@login_required
def overdue_page():
    return render_template("overdue.html")

@app.get("/api/overdue")
@login_required
def overdue_list():
    conn = get_db()
    cur = conn.cursor()

    ten_days_ago = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")

    cur.execute("""
        SELECT * FROM entries
        WHERE status!='Delivered'
        AND receive_date < %s
        ORDER BY receive_date ASC
    """, (ten_days_ago,))

    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([row_to_obj(r) for r in rows])

# ---------------- ENTRY ACTIONS ----------------
@app.post("/api/entries/<int:eid>/action")
@login_required
def entry_action(eid):
    d = request.get_json(force=True)
    a = d.get("action")
    t = now()

    conn = get_db()
    cur = conn.cursor()

    if a == "out":
        cur.execute("UPDATE entries SET status='Out', out_date=%s WHERE id=%s", (t,eid))
    elif a == "in":
        cur.execute("UPDATE entries SET status='In', in_date=%s WHERE id=%s", (t,eid))
    elif a == "ready":
        cur.execute("UPDATE entries SET status='Ready', ready_date=%s WHERE id=%s", (t,eid))
    elif a == "delivered":
        cur.execute("UPDATE entries SET status='Delivered', return_date=%s WHERE id=%s", (t,eid))
    elif a == "reject":
        cur.execute("UPDATE entries SET status='Rejected', reject_date=%s WHERE id=%s", (t,eid))
    else:
        return jsonify({"error":"Invalid action"}),400

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})

# ---------------- BILL ----------------
@app.post("/api/entries/<int:eid>/bill")
@login_required
def save_bill(eid):
    d = request.get_json(force=True)

    bill = {
        "parts": d.get("parts",""),
        "parts_total": float(d.get("parts_total") or 0),
        "service_charge": float(d.get("service_charge") or 0),
        "other": float(d.get("other") or 0),
        "payment_mode": d.get("payment_mode","Cash")
    }

    total = bill["parts_total"] + bill["service_charge"] + bill["other"]

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE entries SET bill_json=%s WHERE id=%s", (json.dumps(bill), eid))
    cur.execute("""
        INSERT INTO sales(sale_date,item,qty,rate,amount,payment_mode,note)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (now(),"Service Bill",1,total,total,bill["payment_mode"],f"Entry {eid}"))

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

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))