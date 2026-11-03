from nar.context.pruning import prune_tool_results
from nar.providers.messages import Message, ToolCall

MARKER = "[result of read_file removed to save context; call the tool again if you need it]"


def exchange(call_id, content, is_error=False):
    return [
        Message.assistant("", [ToolCall(call_id, "read_file", {"path": "a.txt"})]),
        Message.tool_result(call_id, content, is_error),
    ]


def test_old_results_become_the_marker_and_new_ones_stay():
    messages = [Message.user("read"), *exchange("1", "a" * 4000), *exchange("2", "b" * 4000)]
    pruned, count = prune_tool_results(messages, keep_tokens=1000)
    assert count == 1
    assert pruned[2].content == MARKER
    assert pruned[4].content == "b" * 4000


def test_results_older_than_the_first_one_over_budget_are_pruned_even_if_small_enough():
    messages = [*exchange("1", "a" * 400), *exchange("2", "b" * 8000), *exchange("3", "c" * 400)]
    pruned, count = prune_tool_results(messages, keep_tokens=500)
    assert count == 2
    assert pruned[1].content == MARKER
    assert pruned[3].content == MARKER
    assert pruned[5].content == "c" * 400


def test_short_results_and_other_messages_do_not_change():
    messages = [Message.user("u" * 5000), *exchange("1", "short"), Message.assistant("x" * 5000)]
    pruned, count = prune_tool_results(messages, keep_tokens=0)
    assert count == 0
    assert pruned == tuple(messages)


def test_pairing_and_error_flag_are_kept():
    messages = exchange("1", "e" * 4000, is_error=True)
    pruned, _ = prune_tool_results(messages, keep_tokens=0)
    assert pruned[0].tool_calls == messages[0].tool_calls
    assert pruned[1].tool_call_id == "1"
    assert pruned[1].is_error is True


def test_pruning_twice_changes_nothing_more():
    messages = [*exchange("1", "a" * 4000), *exchange("2", "b" * 4000)]
    once, first = prune_tool_results(messages, keep_tokens=0)
    twice, second = prune_tool_results(once, keep_tokens=0)
    assert first == 2
    assert second == 0
    assert twice == once


def test_a_result_without_a_known_call_still_gets_a_marker():
    pruned, count = prune_tool_results([Message.tool_result("x", "a" * 4000)], keep_tokens=0)
    assert count == 1
    assert "the tool" in pruned[0].content
