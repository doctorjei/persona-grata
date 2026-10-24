"""Identifiers shared by agent names and component labels."""


def validate_id(value, kind="agent"):
    """Return an ASCII shell-safe identifier, or refuse it."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"a {kind} name cannot be empty")
    if len(value) > 64:
        raise ValueError(f"invalid {kind} name {value!r}: names are at most 64 characters")
    if not all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in value):
        raise ValueError(
            f"invalid {kind} name {value!r}: names may contain only ASCII letters, digits and '_'")
    if not (value[0].isalpha() or value[0] == "_"):
        raise ValueError(f"invalid {kind} name {value!r}: names must begin with a letter or '_'")
    reserved = {"default"}
    if kind == "agent":
        reserved |= {"if", "then", "else", "elif", "fi", "do", "done", "case", "esac",
                     "while", "until", "for", "in", "function", "select", "time", "coproc"}
    if value in reserved:
        raise ValueError(f"invalid {kind} name {value!r}: name is reserved")
    return value
