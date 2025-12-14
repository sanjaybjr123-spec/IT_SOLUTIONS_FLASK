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
          return_date TEXT,
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
        CREATE TABLE IF NOT EXISTS customers(
          id SERIAL PRIMARY KEY,
          name TEXT,
          phone TEXT,
          address TEXT
        )
        """)

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("DB init skipped:", e)

# safe init
init_db()

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
        "return_date": r["return_date"],
        "status": r["status"],
        "bill": json.loads(r["bill_json"]) if r["bill_json"] else {}
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
    today_sales = cur.fetchone()["s"]

    cur.execute("SELECT COUNT(*) n FROM entries WHERE status!='Delivered'")
    pending = cur.fetchone()["n"]

    cur.close()
    conn.close()

    return render_template(
        "dashboard.html",
        kp={
            "today_sales": today_sales,
            "pending": pending,
            "overdue": 0,
            "ledger_bal": 0
        }
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
    return jsonify({"ok": True}), 201

# ---------------- SAVE BILL (Cash / UPI / Card) ----------------
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

    total_amount = (
        bill["parts_total"] +
        bill["service_charge"] +
        bill["other"]
    )

    conn = get_db()
    cur = conn.cursor()

    # save bill
    cur.execute(
        "UPDATE entries SET bill_json=%s WHERE id=%s",
        (json.dumps(bill), eid)
    )

    # save into sales table
    cur.execute("""
        INSERT INTO sales(sale_date,item,qty,rate,amount,payment_mode,note)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (
        now(),
        "Service Bill",
        1,
        total_amount,
        total_amount,
        bill["payment_mode"],
        f"Entry ID {eid}"
    ))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"ok": True})

# ---------------- DELETE ENTRY (FIXED) ----------------
@app.delete("/api/entries/<int:eid>")
def delete_entry(eid):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("DELETE FROM entries WHERE id=%s", (eid,))
    conn.commit()

    cur.close()
    conn.close()
    return jsonify({"deleted": True})

# ---------------- PRINT RECEIPT ----------------
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

    return render_template(
        "receipt.html",
        e=row_to_obj(r),
        shop={
            "name": "IT SOLUTIONS",
            "addr": "GHATSILA COLLEGE ROAD"
        }
    )

# ---------------- CSV EXPORT ----------------
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
def export_entries():
    return export_csv("SELECT * FROM entries ORDER BY id DESC", "entries.csv")

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))