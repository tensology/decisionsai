import base64, json, os
from django.conf import settings as _s
from django.db import connection, transaction

_P = json.loads(base64.b64decode(os.environ["TS_PARAMS"]).decode())
_MODE = _P.get("_mode", "read")
_DB = _s.DATABASES["default"]["NAME"]
if _MODE != "read" and not str(_DB).startswith("scratch_"):
    raise SystemExit("REFUSED: write scripts only run on a scratch_ database, not %r" % _DB)

with transaction.atomic():
    if _MODE == "read":
        connection.cursor().execute("SET TRANSACTION READ ONLY")
    _out = main(_P)  # noqa: F821  (defined by the script body above this wrapper)
    if _MODE == "read":
        transaction.set_rollback(True)
print("@@RESULT@@" + json.dumps(_out, default=str))
