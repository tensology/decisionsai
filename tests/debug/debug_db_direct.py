import sqlite3
import json
import os

db_path = 'db/settings.db'

if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    print("--- Checking Snippets ---")
    cursor.execute("SELECT id, title, additional_trigger_words FROM snippets")
    rows = cursor.fetchall()
    
    print(f"Total snippets: {len(rows)}")
    
    found_50 = False
    for row in rows:
        id, title, triggers_json = row
        print(f"ID: {id} | Title: '{title}' | Triggers: {triggers_json}")
        
        if '50' in title:
            found_50 = True
        
        try:
            triggers = json.loads(triggers_json) if triggers_json else []
            if '50' in triggers:
                found_50 = True
        except:
            pass

    if found_50:
        print("\n✅ Found snippet matching '50'")
    else:
        print("\n❌ No snippet found matching '50'")

except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()


