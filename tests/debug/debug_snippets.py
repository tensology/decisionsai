from distr.core.db import get_session, Snippet
import json

session = get_session()
try:
    snippets = session.query(Snippet).all()
    print(f"Total snippets: {len(snippets)}")
    
    found = False
    for s in snippets:
        triggers = []
        try:
            triggers = json.loads(s.additional_trigger_words)
        except:
            pass
            
        print(f"ID: {s.id} | Title: '{s.title}' | Triggers: {triggers}")
        
        if '50' in s.title or '50' in triggers:
            found = True
            print(f"MATCH FOUND: ID={s.id}, Title='{s.title}'")

    if not found:
        print("No snippet found matching '50'")
finally:
    session.close()


