import sys
try:
    import torch
    v = torch.__version__.split("+")[0].split(".")
    print(v[0] + "." + v[1])
except Exception:
    sys.exit(1)
