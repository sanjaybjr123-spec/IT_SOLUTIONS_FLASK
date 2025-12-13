from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
import os, json, datetime
import psycopg2
import psycopg2.extras

app = Flask(__name__, template_folder="templates")

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------- DATABASE ----------------
def get_db():
    DATABASE_URL = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor
    )
    return conn

def init_db():
    c = get_db()
    cur = c.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS entries(
      id SERIAL PRIMARY KEY,
      type TEXT, customer TEXT, phone TEXT, model TEXT, problem TEXT,
      receive_date TEXT, out_date TEXT, in_date TEXT, return_date TEXT,
      status TEXT, bill_json TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales(
      id SERIAL PRIMARY KEY,
      sale_date TEXT, item TEXT, qty REAL, rate REAL, amount REAL,
      payment_mode TEXT, note TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ledger(
      id SERIAL PRIMARY KEY,
      tx_date TEXT, party TEXT, tx_type TEXT, amount REAL, note TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers(
      id SERIAL PRIMARY KEY,
      name TEXT, phone TEXT, address TEXT
    )""")

    c.commit()
    cur.close()
    c.close()

def row_to_obj(r):
    if not r:
        return None
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
        "return_date": r["return_date"],
        "status": r["status"],
        "bill": json.loads(r["bill_json"]) if r["bill_json"] else None
    }

# ---------------- DASHBOARD ----------------
@app.route("/")
def dashboard():
    c = get_db()
    cur = c.cursor()

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    cur.execute("SELECT COALESCE(SUM(amount),0) s FROM sales WHERE sale_date LIKE %s", (today+"%",))
    t_sales = cur.fetchone()["s"]

    cur.execute("SELECT COUNT(*) n FROM entries WHERE status!='Delivered'")
    pending = cur.fetchone()["n"]

    overdue = 0
    cur.execute("SELECT receive_date,status FROM entries")
    for r in cur.fetchall():
        if r["status"] != "Delivered" and r["receive_date"]:
            diff = (datetime.datetime.now() - datetime.datetime.strptime(r["receive_date"], "%Y-%m-%d %H:%M:%S")).days
            if diff > 10:
                overdue += 1

    cur.execute("""
    SELECT party, SUM(CASE WHEN tx_type='credit' THEN amount ELSE -amount END) bal
    FROM ledger GROUP BY party
    """)
    parties = cur.fetchall()
    total_bal = sum([p["bal"] for p in parties])

    cur.close()
    c.close()

    return render_template("dashboard.html",
        kp={"today_sales": t_sales, "pending": pending, "overdue": overdue, "ledger_bal": total_bal}
    )

# ---------------- SERVICE ----------------
@app.route("/service")
def service_page():
    return render_template("service.html")

@app.get("/api/entries")
def list_entries():
    c = get_db()
    cur = c.cursor()
    cur.execute("SELECT * FROM entries ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close()
    c.close()
    return jsonify([row_to_obj(r) for r in rows])

@app.post("/api/entries")
def add_entry():
    d = request.get_json(force=True)
    c = get_db()
    cur = c.cursor()
    cur.execute("""
        INSERT INTO entries(type,customer,phone,model,problem,receive_date,status)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (
        d.get("type",""), d.get("customer",""), d.get("phone",""),
        d.get("model",""), d.get("problem",""), now(), "Received"
    ))
    c.commit()
    cur.close()
    c.close()
    return jsonify({"ok": True}), 201

@app.post("/api/entries/<int:eid>/action")
def do_action(eid):
    d = request.get_json(force=True)
    act = d.get("action","")
    t = now()
    c = get_db()
    cur = c.cursor()

    if act == "out":
        cur.execute("UPDATE entries SET out_date=%s,status='Out' WHERE id=%s", (t,eid))
    elif act == "in":
        cur.execute("UPDATE entries SET in_date=%s,status='In' WHERE id=%s", (t,eid))
    elif act == "ready":
        cur.execute("UPDATE entries SET status='Ready' WHERE id=%s", (eid,))
    elif act == "delivered":
        cur.execute("UPDATE entries SET return_date=%s,status='Delivered' WHERE id=%s", (t,eid))
    elif act == "reject":
        cur.execute("UPDATE entries SET status='NOT DONE' WHERE id=%s", (eid,))
    else:
        cur.close(); c.close()
        return ("Invalid action", 400)

    c.commit()
    cur.close()
    c.close()
    return jsonify({"ok": True})

@app.post("/api/entries/<int:eid>/bill")
def save_bill(eid):
    d = request.get_json(force=True)
    bill = {
        "parts": d.get("parts",""),
        "parts_total": float(d.get("parts_total") or 0),
        "service_charge": float(d.get("service_charge") or 0),
        "other": float(d.get("other") or 0),
        "payment_mode": d.get("payment_mode","Cash")
    }
    c = get_db()
    cur = c.cursor()
    cur.execute("UPDATE entries SET bill_json=%s WHERE id=%s", (json.dumps(bill), eid))
    c.commit()
    cur.close()
    c.close()
    return jsonify({"ok": True})

@app.get("/print/<int:eid>")
def print_receipt(eid):
    c = get_db()
    cur = c.cursor()
    cur.execute("SELECT * FROM entries WHERE id=%s", (eid,))
    r = cur.fetchone()
    cur.close()
    c.close()
    if not r:
        abort(404)
    return render_template("receipt.html", e=row_to_obj(r),
        shop={"name":"IT SOLUTIONS","addr":"GHATSILA COLLEGE ROAD"}
    )

@app.delete("/api/entries/<int:eid>")
def del_entry(eid):
    c = get_db()
    cur = c.cursor()
    cur.execute("DELETE FROM entries WHERE id=%s", (eid,))
    c.commit()
    cur.close()
    c.close()
    return jsonify({"deleted": True})

@app.route("/overdue")
def overdue_page():
    return render_template("overdue.html")

# ---------------- SALES ----------------
@app.route("/sales", methods=["GET","POST"])
def sales_page():
    c = get_db()
    cur = c.cursor()
    if request.method == "POST":
        f = request.form
        amt = float(f.get("qty") or 0) * float(f.get("rate") or 0)
        cur.execute("""
            INSERT INTO sales(sale_date,item,qty,rate,amount,payment_mode,note)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (now(), f.get("item",""), f.get("qty") or 0, f.get("rate") or 0,
              amt, f.get("payment","Cash"), f.get("note","")))
        c.commit()

    cur.execute("SELECT * FROM sales ORDER BY id DESC")
    rows = cur.fetchall()
    cur.execute("SELECT COALESCE(SUM(amount),0) t FROM sales")
    total = cur.fetchone()["t"]
    cur.close()
    c.close()
    return render_template("sales.html", rows=rows, total=total)

# ---------------- LEDGER ----------------
@app.route("/ledger", methods=["GET","POST"])
def ledger_page():
    c = get_db()
    cur = c.cursor()
    if request.method == "POST":
        f = request.form
        cur.execute("""
            INSERT INTO ledger(tx_date,party,tx_type,amount,note)
            VALUES (%s,%s,%s,%s,%s)
        """, (now(), f.get("party",""), f.get("tx_type","credit"),
              float(f.get("amount") or 0), f.get("note","")))
        c.commit()

    cur.execute("SELECT * FROM ledger ORDER BY id DESC")
    rows = cur.fetchall()
    cur.execute("""
        SELECT party, SUM(CASE WHEN tx_type='credit' THEN amount ELSE -amount END) bal
        FROM ledger GROUP BY party
    """)
    parties = cur.fetchall()
    cur.close()
    c.close()
    return render_template("ledger.html", rows=rows, parties=parties)

# ---------------- CUSTOMERS ----------------
@app.route("/customers", methods=["GET","POST"])
def customers_page():
    c = get_db()
    cur = c.cursor()
    if request.method == "POST":
        f = request.form
        cur.execute(
            "INSERT INTO customers(name,phone,address) VALUES (%s,%s,%s)",
            (f.get("name",""), f.get("phone",""), f.get("address",""))
        )
        c.commit()

    cur.execute("SELECT * FROM customers ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close()
    c.close()
    return render_template("customers.html", rows=rows)

# ---------------- RUN ----------------
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))