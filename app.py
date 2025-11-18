from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
import sqlite3, os, json, datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(APP_DIR, "data.db")

app = Flask(__name__, template_folder="templates")

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    c = get_db()
    # service entries (same as before)
    c.execute("""
    CREATE TABLE IF NOT EXISTS entries(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      type TEXT, customer TEXT, phone TEXT, model TEXT, problem TEXT,
      receive_date TEXT, out_date TEXT, in_date TEXT, return_date TEXT,
      status TEXT, bill_json TEXT
    )""")
    # sales
    c.execute("""
    CREATE TABLE IF NOT EXISTS sales(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sale_date TEXT, item TEXT, qty REAL, rate REAL, amount REAL,
      payment_mode TEXT, note TEXT
    )""")
    # ledger (credit/debit)
    c.execute("""
    CREATE TABLE IF NOT EXISTS ledger(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      tx_date TEXT, party TEXT, tx_type TEXT, amount REAL, note TEXT
    )""")
    # customers
    c.execute("""
    CREATE TABLE IF NOT EXISTS customers(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT, phone TEXT, address TEXT
    )""")
    c.commit(); c.close()

def row_to_obj(r):
    if not r: return None
    return {
        "id": r["id"], "type": r["type"], "customer": r["customer"], "phone": r["phone"],
        "model": r["model"], "problem": r["problem"],
        "receive_date": r["receive_date"], "out_date": r["out_date"],
        "in_date": r["in_date"], "return_date": r["return_date"],
        "status": r["status"], "bill": json.loads(r["bill_json"]) if r["bill_json"] else None
    }

@app.route("/")
def dashboard():
    c = get_db()
    # KPIs
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    t_sales = c.execute("SELECT COALESCE(SUM(amount),0) AS s FROM sales WHERE substr(sale_date,1,10)=?", (today,)).fetchone()["s"]
    pending = c.execute("SELECT COUNT(*) AS n FROM entries WHERE status!='Delivered'").fetchone()["n"]
    overdue = 0
    for r in c.execute("SELECT receive_date,status FROM entries"):
        if r["status"]!="Delivered" and r["receive_date"]:
            diff=(datetime.datetime.now()-datetime.datetime.strptime(r["receive_date"],"%Y-%m-%d %H:%M:%S")).days
            if diff>10: overdue+=1
    parties = c.execute("SELECT party, SUM(CASE WHEN tx_type='credit' THEN amount ELSE -amount END) bal FROM ledger GROUP BY party").fetchall()
    total_bal = sum([p["bal"] for p in parties])
    c.close()
    return render_template("dashboard.html",
        kp={"today_sales":t_sales,"pending":pending,"overdue":overdue,"ledger_bal":total_bal}
    )

# ---------------- SERVICE (same behaviour) ----------------
@app.route("/service")
def service_page():
    return render_template("service.html")

@app.get("/api/entries")
def list_entries():
    c=get_db(); rows=c.execute("SELECT * FROM entries ORDER BY id DESC").fetchall(); c.close()
    return jsonify([row_to_obj(r) for r in rows])

@app.post("/api/entries")
def add_entry():
    d=request.get_json(force=True)
    c=get_db()
    c.execute("""INSERT INTO entries(type,customer,phone,model,problem,receive_date,status)
                 VALUES (?,?,?,?,?,?,?)""",
              (d.get("type",""),d.get("customer",""),d.get("phone",""),
               d.get("model",""),d.get("problem",""), now(), "Received"))
    c.commit(); c.close()
    return jsonify({"ok":True}),201

@app.post("/api/entries/<int:eid>/action")
def do_action(eid):
    d=request.get_json(force=True); act=d.get("action",""); t=now()
    c=get_db()
    if   act=="out":        c.execute("UPDATE entries SET out_date=?,status='Out' WHERE id=?", (t,eid))
    elif act=="in":         c.execute("UPDATE entries SET in_date=?,status='In' WHERE id=?", (t,eid))
    elif act=="ready":      c.execute("UPDATE entries SET status='Ready' WHERE id=?", (eid,))
    elif act=="delivered":  c.execute("UPDATE entries SET return_date=?,status='Delivered' WHERE id=?", (t,eid))
    elif act=="reject":     c.execute("UPDATE entries SET status='NOT DONE' WHERE id=?", (eid,))
    else: c.close(); return ("Invalid action",400)
    c.commit(); c.close()
    return jsonify({"ok":True})

@app.post("/api/entries/<int:eid>/bill")
def save_bill(eid):
    d=request.get_json(force=True)
    bill={"parts":d.get("parts",""),
          "parts_total":float(d.get("parts_total") or 0),
          "service_charge":float(d.get("service_charge") or 0),
          "other":float(d.get("other") or 0),
          "payment_mode":d.get("payment_mode","Cash")}
    c=get_db(); c.execute("UPDATE entries SET bill_json=? WHERE id=?", (json.dumps(bill),eid))
    c.commit(); c.close(); return jsonify({"ok":True})

@app.get("/print/<int:eid>")
def print_receipt(eid):
    c=get_db(); r=c.execute("SELECT * FROM entries WHERE id=?", (eid,)).fetchone(); c.close()
    if not r: abort(404)
    return render_template("receipt.html", e=row_to_obj(r), shop={"name":"IT SOLUTIONS","addr":"GHATSILA COLLEGE ROAD"})

@app.delete("/api/entries/<int:eid>")
def del_entry(eid):
    c=get_db(); c.execute("DELETE FROM entries WHERE id=?", (eid,)); c.commit(); c.close()
    return jsonify({"deleted":True})

@app.route("/overdue")
def overdue_page():
    return render_template("overdue.html")

# ---------------- SALES ----------------
@app.route("/sales", methods=["GET","POST"])
def sales_page():
    c=get_db()
    if request.method=="POST":
        f=request.form
        amt = float(f.get("qty") or 0)*float(f.get("rate") or 0)
        c.execute("""INSERT INTO sales(sale_date,item,qty,rate,amount,payment_mode,note)
                     VALUES (?,?,?,?,?,?,?)""",
                  (now(), f.get("item",""), f.get("qty") or 0, f.get("rate") or 0, amt, f.get("payment","Cash"), f.get("note","")))
        c.commit()
    rows=c.execute("SELECT * FROM sales ORDER BY id DESC").fetchall()
    total=c.execute("SELECT COALESCE(SUM(amount),0) t FROM sales").fetchone()["t"]
    c.close()
    return render_template("sales.html", rows=rows, total=total)

# ---------------- LEDGER ----------------
@app.route("/ledger", methods=["GET","POST"])
def ledger_page():
    c=get_db()
    if request.method=="POST":
        f=request.form
        c.execute("""INSERT INTO ledger(tx_date,party,tx_type,amount,note)
                     VALUES (?,?,?,?,?)""",
                  (now(), f.get("party",""), f.get("tx_type","credit"), float(f.get("amount") or 0), f.get("note","")))
        c.commit()
    rows=c.execute("SELECT * FROM ledger ORDER BY id DESC").fetchall()
    parties=c.execute("SELECT party, SUM(CASE WHEN tx_type='credit' THEN amount ELSE -amount END) bal FROM ledger GROUP BY party").fetchall()
    c.close()
    return render_template("ledger.html", rows=rows, parties=parties)

# ---------------- CUSTOMERS ----------------
@app.route("/customers", methods=["GET","POST"])
def customers_page():
    c=get_db()
    if request.method=="POST":
        f=request.form
        c.execute("INSERT INTO customers(name,phone,address) VALUES (?,?,?)",
                  (f.get("name",""), f.get("phone",""), f.get("address","")))
        c.commit()
    rows=c.execute("SELECT * FROM customers ORDER BY id DESC").fetchall()
    c.close()
    return render_template("customers.html", rows=rows)

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
