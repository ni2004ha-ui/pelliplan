import os, sys
from datetime import date, timedelta
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app import engine as E

T0 = date(2026, 1, 1)
D = lambda n: T0 + timedelta(days=n)

# The blouse chain from the spec. Hand-computed:
#  1 purchase (3d) -> 2 deliver fabric (4d) -> 3 stitch (10d, can buy back 4d for 4000) -> 4 fitting (1d) -> 5 alterations (3d, due Jan 25)
#  es/ef: 1: Jan1-4, 2: 4-8, 3: 8-18, 4: 18-19, 5: 19-22.  Deadline 25 => slack 3.
def chain(deadline=24):
    nodes = {1: E.Node(1, 3), 2: E.Node(2, 4), 3: E.Node(3, 10, expedite_days=4, expedite_cost=4000),
             4: E.Node(4, 1), 5: E.Node(5, 3, deadline=D(deadline))}
    preds = E.build_preds(nodes, [(2, 1), (3, 2), (4, 3), (5, 4)])
    return nodes, preds

def test_forward_and_backward_pass_match_hand_calculation():
    nodes, preds = chain()
    s = E.schedule(nodes, preds, T0)
    assert (s[3].es, s[3].ef) == (D(7), D(17))
    assert (s[5].es, s[5].ef) == (D(18), D(21))
    assert s[5].slack == 3 and all(s[i].slack == 3 for i in nodes)       # one chain: everyone shares the slack
    assert s[5].tight and not s[5].critical

def test_delay_propagates_and_reports_the_chain():
    nodes, preds = chain()
    r = E.impact(nodes, preds, T0, {2: 5})                                  # delivery slips 5 days
    assert [x["id"] for x in r["affected"]] == [2, 3, 4, 5]                 # the slipped task and everything downstream
    assert [x["id"] for x in r["pressured"]] == [1]                         # upstream: does not move, but has no room left
    assert [(m["id"], m["late_days"]) for m in r["deadline_misses"]] == [(5, 2)]
    five = next(x for x in r["affected"] if x["id"] == 5)
    assert five["chain"] == [2, 3, 4, 5] and five["shift_days"] == 5 and five["slack_after"] == -2

def test_unrelated_task_is_not_affected():
    nodes, preds = chain()
    nodes[9] = E.Node(9, 2, deadline=D(30)); preds[9] = set()
    r = E.impact(nodes, preds, T0, {2: 5})
    assert 9 not in [x["id"] for x in r["affected"]] + [x["id"] for x in r["pressured"]]

def test_a_slip_that_fits_inside_the_slack_misses_nothing():
    nodes, preds = chain()
    r = E.impact(nodes, preds, T0, {2: 2})                                  # slack is 3, so 2 days is absorbed
    assert r["deadline_misses"] == [] and [x["id"] for x in r["affected"]] == [2, 3, 4, 5]

def test_recovery_buys_back_exactly_what_is_needed():
    nodes, preds = chain()
    r = E.plan_recovery(nodes, preds, T0, {2: 5})
    assert r["fully_recovered"] and len(r["steps"]) == 1
    assert (r["steps"][0]["task_id"], r["steps"][0]["cut_days"], r["steps"][0]["cost"]) == (3, 2, 2000.0)
    assert r["total_cost"] == 2000.0

def test_recovery_admits_when_it_cannot_fix_things():
    nodes, preds = chain()
    r = E.plan_recovery(nodes, preds, T0, {2: 12})                           # 9 days late; only 4 can be bought back
    assert not r["fully_recovered"] and r["total_cost"] == 4000.0
    assert r["remaining_late"] == [dict(id=5, late_days=5)]

def test_recovery_prefers_the_cheaper_day():
    nodes, preds = chain()
    nodes[4] = E.Node(4, 1, expedite_days=1, expedite_cost=100)             # 100/day beats 1000/day
    r = E.plan_recovery(nodes, preds, T0, {2: 5})
    assert r["steps"][0]["task_id"] == 4 and r["total_cost"] < 2000

def test_cannot_slip_a_finished_task():
    nodes, preds = chain()
    nodes[1] = E.Node(1, 3, status="done", completed_on=D(-2))
    with pytest.raises(ValueError):
        E.impact(nodes, preds, T0, {1: 2})

def test_done_tasks_do_not_constrain_the_plan():
    nodes, preds = chain()
    nodes[1] = E.Node(1, 3, status="done", completed_on=D(-5))
    s = E.schedule(nodes, preds, T0)
    assert s[2].es == T0 and s[5].slack == 6                                 # chain starts today, 3 days earlier

def test_not_before_delays_the_start():
    nodes, preds = chain()
    nodes[2] = E.Node(2, 4, not_before=D(10))
    s = E.schedule(nodes, preds, T0)
    assert s[2].es == D(10)

def test_a_task_with_two_predecessors_waits_for_the_slower_one():
    nodes = {1: E.Node(1, 2), 2: E.Node(2, 9), 3: E.Node(3, 1, deadline=D(30))}
    preds = E.build_preds(nodes, [(3, 1), (3, 2)])
    assert E.schedule(nodes, preds, T0)[3].es == D(9)

def test_no_deadline_anywhere_means_unconstrained():
    nodes = {1: E.Node(1, 2)}
    s = E.schedule(nodes, E.build_preds(nodes, []), T0)
    assert s[1].slack is None and not s[1].critical

# ---------- graph rules ----------
def test_cycles_are_rejected_with_the_loop_shown():
    with pytest.raises(E.CycleError) as e:
        E.build_preds([1, 2, 3], [(2, 1), (3, 2), (1, 3)])
    assert set(e.value.path) == {1, 2, 3}

def test_self_dependency_and_unknown_ids_are_rejected():
    with pytest.raises(E.CycleError): E.build_preds([1], [(1, 1)])
    with pytest.raises(ValueError): E.build_preds([1], [(1, 2)])

def test_would_create_cycle_detects_before_saving():
    nodes, preds = chain()
    assert E.would_create_cycle(preds, 1, 5) is not None                      # 1 depends on 5 would close the loop
    assert E.would_create_cycle(preds, 5, 1) is None                          # redundant but harmless

def test_depths_for_drawing():
    _, preds = chain()
    assert E.depths(preds) == {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}

def test_ceremony_risk_labels():
    nodes, preds = chain(deadline=24)
    s = E.schedule(nodes, preds, T0)
    assert E.ceremony_risk({i: 7 for i in nodes}, s, nodes) == {7: "tight"}
    s2 = E.impact(nodes, preds, T0, {2: 5})["after"]
    assert E.ceremony_risk({i: 7 for i in nodes}, s2, E.apply_slip(nodes, {2: 5})) == {7: "at_risk"}
