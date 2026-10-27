from nar.tools.validation import validate_arguments

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "offset": {"type": "integer", "minimum": 1},
        "limit": {"type": "integer", "minimum": 1},
    },
    "required": ["path"],
    "additionalProperties": False,
}


def test_valid_call_returns_no_messages():
    assert validate_arguments(SCHEMA, {"path": "a.txt", "offset": 3}) == []


def test_missing_required_argument():
    assert validate_arguments(SCHEMA, {}) == ["missing required argument 'path'"]


def test_each_missing_argument_is_reported_once():
    schema = {"type": "object", "required": ["a", "b"]}
    assert validate_arguments(schema, {}) == [
        "missing required argument 'a'",
        "missing required argument 'b'",
    ]


def test_wrong_type_names_argument_and_types():
    assert validate_arguments(SCHEMA, {"path": "a", "offset": "ten"}) == [
        "argument 'offset' must be of type integer, got string"
    ]


def test_no_type_coercion():
    assert validate_arguments(SCHEMA, {"path": "a", "limit": "10"}) != []


def test_boolean_is_not_an_integer():
    messages = validate_arguments(SCHEMA, {"path": "a", "limit": True})
    assert messages == ["argument 'limit' must be of type integer, got boolean"]


def test_unknown_argument():
    assert validate_arguments(SCHEMA, {"path": "a", "foo": 1}) == ["unknown argument 'foo'"]


def test_every_unknown_argument_is_reported():
    messages = validate_arguments(SCHEMA, {"path": "a", "foo": 1, "bar": 2})
    assert sorted(messages) == ["unknown argument 'bar'", "unknown argument 'foo'"]


def test_minimum_violation_names_argument():
    messages = validate_arguments(SCHEMA, {"path": "a", "offset": 0})
    assert messages == ["argument 'offset': 0 is less than the minimum of 1"]


def test_enum_violation():
    schema = {"type": "object", "properties": {"mode": {"enum": ["a", "b"]}}}
    messages = validate_arguments(schema, {"mode": "c"})
    assert len(messages) == 1
    assert messages[0].startswith("argument 'mode': ")


def test_multiple_problems_are_all_reported():
    messages = validate_arguments(SCHEMA, {"offset": "x", "foo": 1})
    assert len(messages) == 3


def test_nested_argument_uses_dotted_name():
    schema = {
        "type": "object",
        "properties": {"opts": {"type": "object", "properties": {"n": {"type": "integer"}}}},
    }
    messages = validate_arguments(schema, {"opts": {"n": "x"}})
    assert messages == ["argument 'opts.n' must be of type integer, got string"]
