from nar.context.manager import ContextManager
from nar.providers.messages import Message
from nar.session.recorder import SessionRecorder
from nar.session.store import Session, SessionStore


def setup(tmp_path, store=None):
    store = store or SessionStore(tmp_path / "sessions")
    context = ContextManager("system")
    warnings = []
    session = Session.new(tmp_path, "openrouter", "m")
    return SessionRecorder(store, session, context, warnings.append), store, context, warnings


class FailingStore(SessionStore):
    fail = True

    def save(self, session):
        if self.fail:
            raise OSError(13, "Permission denied")
        super().save(session)


def test_save_writes_the_messages_of_the_context(tmp_path):
    recorder, store, context, _ = setup(tmp_path)
    context.add_user("hello")
    recorder.save()
    assert store.load(recorder.session.id).messages == (Message.user("hello"),)


def test_no_messages_writes_nothing_and_removes_an_earlier_file(tmp_path):
    recorder, store, context, _ = setup(tmp_path)
    recorder.save()
    assert store.list() == ([], 0)
    mark = context.mark()
    context.add_user("cancelled soon")
    recorder.save()
    assert len(store.list()[0]) == 1
    context.rollback(mark)
    recorder.save()
    assert store.list() == ([], 0)


def test_saved_messages_follow_a_replaced_history(tmp_path):
    recorder, store, context, _ = setup(tmp_path)
    context.add_user("a long conversation")
    recorder.save()
    context.replace_messages([Message.user("summary")])
    recorder.save()
    assert store.load(recorder.session.id).messages == (Message.user("summary"),)


def test_a_failing_save_warns_once_and_again_only_after_a_success(tmp_path):
    store = FailingStore(tmp_path / "sessions")
    recorder, _, context, warnings = setup(tmp_path, store)
    context.add_user("hello")
    recorder.save()
    recorder.save()
    assert warnings == ["warning: cannot save the session: Permission denied"]
    store.fail = False
    recorder.save()
    store.fail = True
    recorder.save()
    assert len(warnings) == 2


def test_switch_moves_later_saves_to_the_new_session(tmp_path):
    recorder, store, context, _ = setup(tmp_path)
    context.add_user("first session")
    recorder.save()
    first = recorder.session
    other = Session("other", "a", "a", "/x", "p", "m", (Message.user("second session"),))
    recorder.switch(other)
    assert context.messages == other.messages
    context.add_user("more")
    recorder.save()
    assert store.load("other").messages[-1] == Message.user("more")
    assert store.load(first.id).messages == (Message.user("first session"),)
