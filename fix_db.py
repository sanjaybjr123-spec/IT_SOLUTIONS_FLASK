import sqlite3

DB_PATH = "data.db"

conn = sqlite3.connect(DB_PATH)

# सभी कॉलम जो नए कोड में चाहिए
columns = ["out_date", "in_date", "return_date", "bill_json"]

for col in columns:
    try:
        conn.execute(f"ALTER TABLE entries ADD COLUMN {col} TEXT;")
        print(f"✅ Added column: {col}")
    except sqlite3.OperationalError:
        print(f"⚠️ Column already exists: {col}")

conn.commit()
conn.close()

print("\n🎯 Database structure updated successfully!")
