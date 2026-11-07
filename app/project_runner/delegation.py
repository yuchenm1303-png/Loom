"""Project episodes have one admission, cancellation and workspace owner.

General chat delegation stays available. Inside a project, only the durable DAG
scheduler may create execution branches; a generic child would share a checkout
and bypass both episode admission and independent task review.
"""
from . import goal_lines
from .budget import event_tokens


def owner(service, session_id):
    if session_id in service._project_session_ids:
        return session_id
    graph = getattr(service.runtime, "agent_graph", None)
    if graph is None:
        return None
    root = graph.root_for_session(session_id)
    return root if root in service._project_session_ids else None


def install(service):
    previous = getattr(service.runtime, "agent_delegation_guard", None)

    def guard(caller, target=None):
        if previous:
            previous(caller, target)
        if owner(service, caller) or (target and owner(service, target)):
            raise ValueError("Build tasks must be assigned by the persistent project scheduler. "
                             "Do not spawn or wake generic sub-agents inside a project episode; "
                             "use the project's dependency plan and worker limit.")
        if goal_lines.is_line(service, caller):
            raise ValueError("A line of a long goal works by itself: its limits would not hold over sub-agents of its own.")

    service.runtime.agent_delegation_guard = guard


def stop_descendants(service, session_ids):
    """Also catches branches created by older Hosts, including already queued work."""
    graph = getattr(service.runtime, "agent_graph", None)
    control = getattr(service.runtime, "agent_control", None)
    if graph is None or control is None:
        return
    for parent in session_ids:
        for node in reversed(graph.list_tree(parent, include_closed=True)):
            try:
                control.close_agent(parent, node.session_id)
            except FileNotFoundError:
                # A deleted historical session must not prevent closing its live
                # siblings/parents. Its graph record still blocks future wakeups.
                graph.close(node.session_id)


def reconcile(service):
    """Quarantine pre-upgrade branches and import their reported usage exactly once."""
    graph = getattr(service.runtime, "agent_graph", None)
    if graph is None:
        return
    roots = tuple(service._project_session_ids)
    stop_descendants(service, roots)
    for root in roots:
        for node in graph.list_tree(root, include_closed=True):
            try:
                events = service.store.events(node.session_id)
            except FileNotFoundError:
                continue
            for event in events:
                if event_tokens(event) is not None:
                    service.project_budget.observe(event)
    service.project_budget.apply_late()
