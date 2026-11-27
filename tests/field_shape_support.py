"""Check that a Field shape's JSON Schema and its own admission agree on omission."""
import copy

from webapp.ai_response_contracts import ContractViolation


def sample(field):
    """A minimal value the shape admits, with every key and one array entry present."""
    if field.kind == "object":
        return {key: sample(child) for key, child in field.fields}
    if field.kind == "array":
        return [sample(field.item)]
    if field.kind == "string":
        return field.enum[0] if field.enum else "x"
    if field.kind == "boolean":
        return True
    return max(field.minimum if field.minimum is not None else 1, 1)


def object_nodes(field, path=()):
    """Every object node with the value path that reaches it (0 for an array entry)."""
    if field.kind == "object":
        yield path, field
        for key, child in field.fields:
            yield from object_nodes(child, path + (key,))
    elif field.kind == "array":
        yield from object_nodes(field.item, path + (0,))


def admits(field, value):
    missing = []
    try:
        field.inspect(value, "", missing)
    except ContractViolation:
        return False
    return not missing


def omission_mismatches(root):
    """Keys where schema() 'required' disagrees with what inspect() accepts left out."""
    base = sample(root)
    assert admits(root, base), "sample must be admitted"
    mismatches = []
    for path, node in object_nodes(root):
        required = set(node.schema()["required"])
        for key, _ in node.fields:
            value = copy.deepcopy(base)
            target = value
            for step in path:
                target = target[step]
            del target[key]
            if admits(root, value) == (key in required):
                mismatches.append((path, key))
    return mismatches
