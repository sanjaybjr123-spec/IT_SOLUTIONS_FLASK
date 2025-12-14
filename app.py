from flask import Flask, render_template, request, jsonify, abort, Response, session, redirect, url_for
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

        # entries
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

        # sales
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sales(
          id SERIAL PRIMARY KEY,
          sale_date TEXT, item TEXT, qty REAL, rate REAL,
          amount REAL, payment_mode TEXT, note TEXT
        )
        """)

        # users
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
          id SERIAL PRIMARY KEY,
          username TEXT UNIQUE,
          password_hash TEXT,
          role TEXT
        )
        """)

        # activity log
        cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_log(
          id SERIAL PRIMARY KEY,
          user_id INTEGER,
          action TEXT,
          action_time TEXT
        )
        """)

        # create default admin if none
        cur.execute("SELECT COUNT(*) AS c FROM users")
        if cur.fetchone()["c"] == 0:
            cur.execute("""
                INSERT INTO users(username, password_hash, role)
                VALUES (%s,%s,%s)
            """, (
                "admin",
                generate_password_hash("admin@123"),
                "admin"
            ))

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("DB init skipped:", e)

init_db()

# ---------------- AUTH HELPERS ----------------
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

# ---------------- ACTIVITY LOG HELPER ----------------
def log_action(user_id, action_text):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO activity_log(user_id, action, action_time) VALUES (%s,%s,%s)",
            (user_id, action_text, now())
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        # For safety, do not break main process
        print("Activity log failed:", e)

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
            log_action(user["id"], f"Logged in")
            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")

@app.route("/logout")
def logout():
    uid = session.get("user_id")
    session.clear()
    if uid:
        log_action(uid, "Logged out")
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
    cur.execute("SELECT COALESCE(SUM(amount),0) s FROM sales WHERE sale_date LIKE %s", (today+"%",))
    today_sales = cur.fetchone()["s"]

    cur.execute("SELECT COUNT(*) n FROM entries WHERE status!='Delivered'")
    pending = cur.fetchone()["n"]

    cur.close()
    conn.close()

    return render_template("dashboard.html", kp={
        "today_sales": today_sales,
        "pending": pending,
        "overdue": 0,
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

    # log
    log_action(session.get("user_id"), f"Added entry")
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

    if a == "out":
        cur.execute("UPDATE entries SET status='Out', out_date=%s WHERE id=%s", (t,eid))
        action_text = f"Marked Out entry {eid}"
    elif a == "in":
        cur.execute("UPDATE entries SET status='In', in_date=%s WHERE id=%s", (t,eid))
        action_text = f"Marked In entry {eid}"
    elif a == "ready":
        cur.execute("UPDATE entries SET status='Ready', ready_date=%s WHERE id=%s", (t,eid))
        action_text = f"Marked Ready entry {eid}"
    elif a == "delivered":
        cur.execute("UPDATE entries SET status='Delivered', return_date=%s WHERE id=%s", (t,eid))
        action_text = f"Delivered entry {eid}"
    elif a == "reject":
        cur.execute("UPDATE entries SET status='Rejected', reject_date=%s WHERE id=%s", (t,eid))
        action_text = f"Rejected entry {eid}"
    else:
        return jsonify({"error":"Invalid action"}),400

    conn.commit()
    cur.close()
    conn.close()

    log_action(session.get("user_id"), action_text)
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

    log_action(session.get("user_id"), f"Saved bill for entry {eid}")
    return jsonify({"ok": True})

# ---------------- DELETE (ADMIN ONLY) ----------------
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

    log_action(session.get("user_id"), f"Deleted entry {eid}")
    return jsonify({"deleted": True})

# ---------------- CSV EXPORT / BACKUP (ADMIN ONLY) ----------------
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
    log_action(session.get("user_id"), "Exported entries CSV")
    return export_csv("SELECT * FROM entries ORDER BY id DESC", "entries_backup.csv")

@app.get("/export/sales")
@login_required
@admin_required
def export_sales():
    log_action(session.get("user_id"), "Exported sales CSV")
    return export_csv("SELECT * FROM sales ORDER BY id DESC", "sales_backup.csv")

@app.get("/export/users")
@login_required
@admin_required
def export_users():
    log_action(session.get("user_id"), "Exported users CSV")
    return export_csv("SELECT id,username,role FROM users", "users_backup.csv")

# ---------------- CHANGE PASSWORD ----------------
@app.route("/change-password", methods=["GET","POST"])
@login_required
def change_password():
    msg = None
    if request.method == "POST":
        old = request.form.get("old_password")
        new = request.form.get("new_password")
        confirm = request.form.get("confirm_password")

        if not old or not new or not confirm:
            msg = "All fields required"
        elif new != confirm:
            msg = "New password mismatch"
        else:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE id=%s", (session.get("user_id"),))
            user = cur.fetchone()
            if user and check_password_hash(user["password_hash"], old):
                new_hash = generate_password_hash(new)
                cur.execute("UPDATE users SET password_hash=%s WHERE id=%s",
                            (new_hash, session.get("user_id")))
                conn.commit()
                msg = "Password updated! Please login again."
                # log
                log_action(session.get("user_id"), "Changed password")
                cur.close()
                conn.close()
                session.clear()
                return render_template("login.html", error=msg)
            else:
                msg = "Old password incorrect"
            cur.close()
            conn.close()

    return render_template("change_password.html", msg=msg)

# ---------------- MANAGE USERS (ADMIN ONLY) ----------------
@app.route("/users", methods=["GET","POST"])
@login_required
@admin_required
def manage_users():
    msg = None
    if request.method == "POST":
        # Add user
        u = request.form.get("username")
        p = request.form.get("password")
        role = request.form.get("role")
        if u and p and role:
            conn = get_db()
            cur = conn.cursor()
            try:
                cur.execute("""
                    INSERT INTO users(username, password_hash, role)
                    VALUES (%s,%s,%s)
                """, (u, generate_password_hash(p), role))
                conn.commit()
                msg = "User added"
                log_action(session.get("user_id"), f"Added user {u} ({role})")
            except Exception as e:
                msg = "Username exists"
            cur.close()
            conn.close()
    # fetch users list
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, username, role FROM users ORDER BY id")
    users = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("users.html", users=users, msg=msg)

# ---------------- RESTORE BACKUP (ADMIN ONLY) ----------------
@app.route("/restore", methods=["GET","POST"])
@login_required
@admin_required
def restore_page():
    msg = None
    if request.method == "POST":
        # expect CSV with header same as entries or sales or users
        f = request.files.get("file")
        if f:
            # detect type from form select
            mode = request.form.get("mode")
            data = f.read().decode("utf-8")
            reader = csv.DictReader(StringIO(data))
            conn = get_db()
            cur = conn.cursor()
            count = 0
            try:
                if mode == "entries":
                    for row in reader:
                        # Insert with minimal fields; ignore id if present
                        cur.execute("""
                            INSERT INTO entries(type,customer,phone,model,problem,receive_date,
                                                out_date,in_date,ready_date,return_date,reject_date,status,bill_json)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """, (
                            row.get("type",""), row.get("customer",""), row.get("phone",""),
                            row.get("model",""), row.get("problem",""), row.get("receive_date"),
                            row.get("out_date"), row.get("in_date"), row.get("ready_date"),
                            row.get("return_date"), row.get("reject_date"),
                            row.get("status"), row.get("bill_json")
                        ))
                        count += 1
                elif mode == "sales":
                    for row in reader:
                        cur.execute("""
                            INSERT INTO sales(sale_date,item,qty,rate,amount,payment_mode,note)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """, (
                            row.get("sale_date",""), row.get("item",""), row.get("qty") or 0,
                            row.get("rate") or 0, row.get("amount") or 0,
                            row.get("payment_mode",""), row.get("note","")
                        ))
                        count += 1
                elif mode == "users":
                    for row in reader:
                        # For users, password not included; skip or set default
                        # Here we skip inserting (could be handled differently)
                        continue
                conn.commit()
                msg = f"Restored {count} rows"
                log_action(session.get("user_id"), f"Restored {count} rows ({mode})")
            except Exception as e:
                msg = "Restore failed"
            cur.close()
            conn.close()
    return render_template("restore.html", msg=msg)

# ---------------- ACTIVITY LOG VIEW (ADMIN ONLY) ----------------
@app.route("/activity")
@login_required
@admin_required
def activity_view():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.id, u.username, a.action, a.action_time
        FROM activity_log a
        LEFT JOIN users u ON a.user_id=u.id
        ORDER BY a.id DESC
        LIMIT 200
    """)
    logs = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("activity.html", logs=logs)

# ---------------- PRINT ----------------
@app.get("/print/<int:eid>")
@login_required
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