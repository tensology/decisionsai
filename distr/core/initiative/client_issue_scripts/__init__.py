"""Fixed scripts the client-issue pipeline runs inside the Tensology app (manage.py shell).

They are shipped as text and never built from model output. Read scripts run in a READ ONLY
transaction that is rolled back. Write scripts refuse to run unless the database is a scratch copy.
"""
