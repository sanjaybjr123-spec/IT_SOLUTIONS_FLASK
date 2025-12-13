from flask import Flask, render_template, request, jsonify, abort, Response
import os, json, datetime, csv
from io import StringIO
import psycopg2
import psycopg2.extras

# ---------------- APP ----------------
app = Flask(__name__, template_folder="templates")

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

    conn.commit()
    cur.close()
    conn.close()

# 🔥 Render fix
init_db()

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
        "return_date": r["return_date"],
        "status": r["status"],
        "bill": json.loads(r["bill_json"]) if r["bill_json"] else None
    }

# ---------------- DASHBOARD ----------------
@app.route("/")
def dashboard():
    conn = get_db()
    cur = conn.cursor()

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    cur.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM sales WHERE sale_date LIKE %s",
        (today + "%",)
    )
    t_sales = cur.fetchone()["s"]

    cur.execute("SELECT COUNT(*) n FROM entries WHERE status!='Delivered'")
    pending = cur.fetchone()["n"]

    overdue = 0
    cur.execute("SELECT receive_date,status FROM entries")
    for r in cur.fetchall():
        if r["status"] != "Delivered" and r["receive_date"]:
            diff = (datetime.datetime.now() -
                    datetime.datetime.strptime(r["receive_date"], "%Y-%m-%d %H:%M:%S")).days
            if diff > 10:
                overdue += 1

    cur.execute("""
    SELECT party, SUM(CASE WHEN tx_type='credit' THEN amount ELSE -amount END) bal
    FROM ledger GROUP BY party
    """)
    parties = cur.fetchall()
    total_bal = sum([p["bal"] for p in parties])

    cur.close()
    conn.close()

    return render_template("dashboard.html",
        kp={"today_sales": t_sales, "pending": pending,
            "overdue": overdue, "ledger_bal": total_bal}
    )

# ---------------- SERVICE ----------------
@app.route("/service")
def service_page():
    return render_template("service.html")

@app.get("/api/entries")
def list_entries():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([row_to_obj(r) for r in rows])

@app.post("/api/entries")
def add_entry():
    d = request.get_json(force=True)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO entries(type,customer,phone,model,problem,receive_date,status)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (
        d.get("type",""), d.get("customer",""), d.get("phone",""),
        d.get("model",""), d.get("problem",""), now(), "Received"
    ))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True}), 201

@app.post("/api/entries/<int:eid>/action")
def do_action(eid):
    d = request.get_json(force=True)
    act = d.get("action","")
    t = now()

    conn = get_db()
    cur = conn.cursor()

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
        cur.close(); conn.close()
        return ("Invalid action", 400)

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})

# ---------------- CSV EXPORT (OPTION 2) ----------------
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
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

@app.get("/export/entries")
def export_entries():
    return export_csv("SELECT * FROM entries ORDER BY id DESC", "entries.csv")

@app.get("/export/sales")
def export_sales():
    return export_csv("SELECT * FROM sales ORDER BY id DESC", "sales.csv")

@app.get("/export/customers")
def export_customers():
    return export_csv("SELECT * FROM customers ORDER BY id DESC", "customers.csv")

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))