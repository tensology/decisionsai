def main(P):
    """Create or drop a scratch_ copy of the production database, using the app's own connection
    settings. Production is only read (pg_dump). Never prints credentials."""
    import os, re, subprocess
    from django.conf import settings
    name = P["name"]
    if not re.match(r"^scratch_[a-z0-9_]{3,40}$", name):
        raise SystemExit("REFUSED: not a scratch_ name")
    d = settings.DATABASES["default"]
    env = dict(os.environ, PGPASSWORD=str(d.get("PASSWORD") or ""))
    base = ["-h", str(d.get("HOST") or "localhost"), "-p", str(d.get("PORT") or "5432"), "-U", str(d.get("USER") or "postgres")]
    if P["action"] == "drop":
        r = subprocess.run(["dropdb", *base, name], env=env, capture_output=True, text=True, timeout=120)
        return {"dropped": r.returncode == 0, "err": r.stderr[-200:]}
    source = str(d.get("NAME"))
    r = subprocess.run(["createdb", *base, name], env=env, capture_output=True, text=True, timeout=120)
    if r.returncode:
        return {"created": False, "err": r.stderr[-300:]}
    dump = subprocess.Popen(["pg_dump", *base, "--no-owner", "--no-privileges", source], env=env, stdout=subprocess.PIPE)
    load = subprocess.run(["psql", "-q", *base, "-d", name], env=env, stdin=dump.stdout, capture_output=True, text=True, timeout=900)
    dump.stdout.close()
    dump.wait()
    return {"created": True, "dump_rc": dump.returncode, "load_rc": load.returncode, "err": load.stderr[-300:] if load.returncode else ""}
