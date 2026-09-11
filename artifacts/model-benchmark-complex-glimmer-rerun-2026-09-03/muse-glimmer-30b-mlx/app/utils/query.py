TRUE_QUERY_VALUES = frozenset({"1", "true", "yes", "on"})


def query_flag(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUE_QUERY_VALUES
