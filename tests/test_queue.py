from server.queue_logic import Entry, can_remove, insert_index, next_to_play, position_at


def add(q, order, guest, eid, status="waiting"):
    if guest not in order:
        order.append(guest)
    i = insert_index(q, guest, order)
    q.insert(i, Entry(eid, guest, position_at(q, i), status))


def ids(q):
    return [e.id for e in q]


def test_round_robin_in_first_queued_order():
    q, order = [], []
    for g, eid in [("A", "a1"), ("A", "a2"), ("A", "a3"), ("B", "b1"), ("C", "c1"), ("B", "b2")]:
        add(q, order, g, eid)
    assert ids(q) == ["a1", "b1", "c1", "a2", "b2", "a3"]
    assert [e.position for e in q] == sorted(e.position for e in q)


def test_late_guest_takes_next_open_turn():
    q, order = [], []
    for g, eid in [("A", "a1"), ("B", "b1"), ("A", "a2"), ("B", "b2")]:
        add(q, order, g, eid)
    q.pop(0)   # a1 was sung
    add(q, order, "C", "c1")
    assert ids(q) == ["b1", "a2", "c1", "b2"]


def test_manual_move_sticks_after_later_inserts():
    q, order = [], []
    for g, eid in [("A", "a1"), ("B", "b1"), ("A", "a2")]:
        add(q, order, g, eid)
    a2 = next(e for e in q if e.id == "a2")
    a2.position = position_at(q, 0, exclude_id="a2")   # host moves a2 to the top
    q.sort(key=lambda e: e.position)
    assert ids(q) == ["a2", "a1", "b1"]
    add(q, order, "C", "c1")
    # the manual order stays, and C's first turn doesn't overtake B's first turn
    assert ids(q) == ["a2", "a1", "b1", "c1"]
    add(q, order, "B", "b2")
    assert ids(q) == ["a2", "a1", "b1", "c1", "b2"]


def test_position_at_ends_and_middle():
    q = [Entry(1, "A", 1.0), Entry(2, "B", 2.0)]
    assert position_at(q, 0) < 1.0
    assert 1.0 < position_at(q, 1) < 2.0
    assert position_at(q, 2) > 2.0
    assert position_at([], 0) == 1.0


def test_unready_head_stays_and_first_ready_plays():
    q = [Entry(1, "A", 1, "preparing"), Entry(2, "B", 2, "waiting"), Entry(3, "C", 3, "ready"),
         Entry(4, "A", 4, "ready")]
    assert next_to_play(q).id == 3
    assert q[0].id == 1   # head untouched
    q[0].status = "ready"
    assert next_to_play(q).id == 1
    assert next_to_play([Entry(1, "A", 1, "failed")]) is None


def test_permissions():
    assert can_remove("g1", "g1", False)
    assert not can_remove("g1", "g2", False)
    assert not can_remove("g1", None, False)
    assert can_remove("g1", None, True)
