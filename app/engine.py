"""The Wedding Dependency Engine. Pure functions: no database, no AI, no clock. Same inputs, same answer.

A wedding is a graph: task A "depends on" task B means A cannot start until B has finished.
From that graph the engine computes, for every task:
  es / ef   earliest start / finish (forward pass, given today's date and durations)
  ls / lf   latest start / finish that still meets every deadline downstream (backward pass)
  slack     ls - es, in days. Negative slack means a deadline will be missed.
Then it can answer "what happens if THIS slips by N days?" and propose a recovery plan.
Simplification (documented in docs/DESIGN.md): durations are calendar days, not working days."""
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Optional

TIGHT_DAYS = 3  # slack at or below this is "tight"


class CycleError(ValueError):
    def __init__(self, path):
        self.path = list(path)
        super().__init__("circular dependency: " + " -> ".join(str(p) for p in self.path))


@dataclass(frozen=True)
class Node:
    id: int
    duration: int                      # days of work still needed
    deadline: Optional[date] = None    # effective deadline (own due date, else its ceremony's date)
    status: str = "todo"               # todo | in_progress | done | blocked
    completed_on: Optional[date] = None
    not_before: Optional[date] = None  # cannot start earlier (for example, the vendor is unavailable)
    expedite_days: int = 0             # how many days could be bought back by paying extra
    expedite_cost: float = 0.0         # total cost of buying back ALL expedite_days


@dataclass
class Sched:
    id: int
    es: date
    ef: date
    ls: Optional[date]
    lf: Optional[date]
    slack: Optional[int]               # None = nothing downstream has a deadline
    late_days: int                     # days past its OWN deadline
    critical: bool                     # slack <= 0
    tight: bool                        # slack <= TIGHT_DAYS


# ---------------------------------------------------------------- graph basics
def build_preds(ids, edges):
    """edges: iterable of (task_id, depends_on_id). Returns {id: set(pred ids)}; rejects unknown ids and cycles."""
    ids = set(ids)
    preds = {i: set() for i in ids}
    for t, d in edges:
        if t not in ids or d not in ids:
            raise ValueError(f"dependency refers to an unknown task: {t} -> {d}")
        if t == d:
            raise CycleError([t, t])
        preds[t].add(d)
    topo_order(ids, preds)  # raises CycleError if the graph has a cycle
    return preds


def topo_order(ids, preds):
    indeg = {i: len(preds[i]) for i in ids}
    succs = invert(preds)
    q = deque(sorted(i for i in ids if indeg[i] == 0))
    out = []
    while q:
        t = q.popleft()
        out.append(t)
        for s in sorted(succs[t]):
            indeg[s] -= 1
            if indeg[s] == 0:
                q.append(s)
    if len(out) != len(ids):
        raise CycleError(_find_cycle(ids, preds))
    return out


def _find_cycle(ids, preds):
    color, stack = {}, []

    def dfs(u):
        color[u] = 1
        stack.append(u)
        for v in sorted(preds[u]):
            if color.get(v) == 1:
                return stack[stack.index(v):] + [v]
            if color.get(v) is None:
                r = dfs(v)
                if r:
                    return r
        stack.pop()
        color[u] = 2
        return None
    for i in sorted(ids):
        if color.get(i) is None:
            r = dfs(i)
            if r:
                return r
    return []


def invert(preds):
    succs = defaultdict(set)
    for t, ps in preds.items():
        succs[t]  # make sure every id has an entry
        for p in ps:
            succs[p].add(t)
    return succs


def ancestors(task, preds):
    seen, stack = set(), [task]
    while stack:
        for p in preds[stack.pop()]:
            if p not in seen:
                seen.add(p)
                stack.append(p)
    return seen


def would_create_cycle(preds, task, depends_on):
    """Would 'task depends on depends_on' create a loop? Returns the loop as a list of ids, or None."""
    if task == depends_on:
        return [task, task]
    if task in ancestors(depends_on, preds) or task == depends_on:
        trial = {k: set(v) for k, v in preds.items()}
        trial[task].add(depends_on)
        return _find_cycle(set(trial), trial)
    return None


def depths(preds):
    """Layer index for drawing: 0 for tasks with no predecessors, else 1 + the deepest predecessor."""
    memo = {}

    def d(t):
        if t not in memo:
            memo[t] = 0 if not preds[t] else 1 + max(d(p) for p in preds[t])
        return memo[t]
    return {t: d(t) for t in preds}


# ---------------------------------------------------------------- scheduling
def schedule(nodes, preds, today):
    """nodes: {id: Node}. Returns {id: Sched}."""
    order = topo_order(set(nodes), preds)
    succs = invert(preds)
    es, ef = {}, {}
    for t in order:
        n = nodes[t]
        if n.status == "done":
            es[t] = ef[t] = n.completed_on or today
            continue
        start = max([today] + ([n.not_before] if n.not_before else []) + [ef[p] for p in preds[t]])
        es[t], ef[t] = start, start + timedelta(days=max(n.duration, 0))
    ls, lf = {}, {}
    for t in reversed(order):
        n = nodes[t]
        if n.status == "done":
            ls[t] = lf[t] = None
            continue
        cands = ([n.deadline] if n.deadline else []) + [ls[s] for s in succs[t] if nodes[s].status != "done" and ls[s]]
        lf[t] = min(cands) if cands else None
        ls[t] = lf[t] - timedelta(days=max(n.duration, 0)) if lf[t] else None
    out = {}
    for t in order:
        n = nodes[t]
        slack = (ls[t] - es[t]).days if ls[t] is not None else None
        late = max(0, (ef[t] - n.deadline).days) if (n.deadline and n.status != "done") else 0
        out[t] = Sched(t, es[t], ef[t], ls[t], lf[t], slack, late,
                       critical=slack is not None and slack <= 0,
                       tight=slack is not None and slack <= TIGHT_DAYS)
    return out


# ---------------------------------------------------------------- what-if
def apply_slip(nodes, slips):
    """slips: {task_id: extra_days}. A slip adds days to the work still needed on that task."""
    out = dict(nodes)
    for t, extra in slips.items():
        if t not in out:
            raise ValueError(f"unknown task {t}")
        if out[t].status == "done":
            raise ValueError("this task is already done, so it cannot slip")
        out[t] = replace(out[t], duration=out[t].duration + extra)
    return out


def _chain_to(task, slipped, after, preds):
    """Trace why `task` moved: follow, at each step, the predecessor that finishes last, back to the slipped task."""
    chain, cur, guard = [task], task, 0
    while cur not in slipped and guard < 200:
        guard += 1
        ps = [p for p in preds[cur] if p in after]
        if not ps:
            break
        nxt = max(ps, key=lambda p: (after[p].ef, -p))
        if after[nxt].ef <= after[cur].es and after[cur].es != after[nxt].ef:
            break  # this task's start is set by today or not_before, not by a predecessor
        chain.append(nxt)
        cur = nxt
    return list(reversed(chain)) if chain[-1] in slipped else [task]


def impact(nodes, preds, today, slips):
    """What moves if `slips` happen?
      affected         tasks whose finish date moves later (the slipped task and everything downstream), with the chain of why
      pressured        upstream tasks that do not move but now have less room: they must finish sooner to save the deadline
      deadline_misses  tasks that will finish after their OWN deadline"""
    before = schedule(nodes, preds, today)
    after = schedule(apply_slip(nodes, slips), preds, today)
    order = topo_order(set(nodes), preds)
    affected, pressured, misses = [], [], []
    for t in order:
        b, a = before[t], after[t]
        if nodes[t].status == "done":
            continue
        shift = (a.ef - b.ef).days
        less_room = (a.slack is not None and b.slack is None) or (a.slack is not None and b.slack is not None and a.slack < b.slack)
        if shift > 0:
            affected.append(dict(id=t, shift_days=shift, ef_before=b.ef, ef_after=a.ef, slack_before=b.slack, slack_after=a.slack,
                                 late_days_before=b.late_days, late_days_after=a.late_days,
                                 chain=_chain_to(t, set(slips), after, preds)))
        elif less_room:
            pressured.append(dict(id=t, slack_before=b.slack, slack_after=a.slack))
        if a.late_days > 0:
            misses.append(dict(id=t, late_days=a.late_days, newly=a.late_days > b.late_days,
                               chain=_chain_to(t, set(slips), after, preds)))
    return dict(before=before, after=after, affected=affected, pressured=pressured, deadline_misses=misses,
                newly_missing=[m["id"] for m in misses if m["newly"]])


def plan_recovery(nodes, preds, today, slips, max_steps=25):
    """Greedy and deterministic: while some task misses its own deadline, buy back days on the cheapest task in that
    task's chain that can be expedited. It reports what it could NOT fix. It only proposes; a human approves."""
    cur = apply_slip(nodes, slips)
    steps = []
    for _ in range(max_steps):
        sch = schedule(cur, preds, today)
        late = [t for t, s in sch.items() if cur[t].status != "done" and s.late_days > 0]
        if not late:
            break
        worst = max(late, key=lambda t: (sch[t].late_days, -t))
        need = sch[worst].late_days
        pool = ancestors(worst, preds) | {worst}
        cands = [t for t in pool if cur[t].status != "done" and cur[t].expedite_days > 0
                 and sch[t].slack is not None and sch[t].slack <= sch[worst].slack]
        if not cands:
            break
        t = min(cands, key=lambda c: (cur[c].expedite_cost / cur[c].expedite_days, c))
        n = cur[t]
        cut = min(n.expedite_days, need)
        cost = round(n.expedite_cost * cut / n.expedite_days, 2)
        steps.append(dict(task_id=t, cut_days=cut, cost=cost, resolves=worst))
        cur[t] = replace(n, duration=n.duration - cut, expedite_days=n.expedite_days - cut,
                         expedite_cost=n.expedite_cost - cost)
    final = schedule(cur, preds, today)
    remaining = sorted((t, final[t].late_days) for t in final if cur[t].status != "done" and final[t].late_days > 0)
    return dict(steps=steps, total_cost=round(sum(x["cost"] for x in steps), 2),
                remaining_late=[dict(id=t, late_days=d) for t, d in remaining], fully_recovered=not remaining, final=final)


# ---------------------------------------------------------------- summaries
def ceremony_risk(task_ceremony, sched, nodes):
    """task_ceremony: {task_id: ceremony_id}. Returns {ceremony_id: 'at_risk' | 'tight' | 'ok'}."""
    worst = {}
    for t, c in task_ceremony.items():
        if nodes[t].status == "done" or c is None:
            continue
        s = sched[t].slack
        if s is not None:
            worst[c] = min(worst.get(c, s), s)
    return {c: ("at_risk" if w < 0 else "tight" if w <= TIGHT_DAYS else "ok") for c, w in worst.items()}
