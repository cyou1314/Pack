"""Conservative comparison of the fixed migration DDL token streams."""
import re


def normalize_ddl(sql):
    # Preserve quoted literals and identifiers exactly. Never collapse token
    # boundaries or remove text inside string values.
    tokens = re.findall(r"""'(?:''|[^'])*'|"(?:""|[^"])*"|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[^\s]""", sql)
    tokens = [t if t.startswith(("'", '"')) else t.lower() for t in tokens]
    if tokens[:1] == ["create"]:
        i = 2 if tokens[1:2] != ["unique"] else 3
        if tokens[i:i+3] == ["if","not","exists"]:
            del tokens[i:i+3]
    return tuple(tokens)
