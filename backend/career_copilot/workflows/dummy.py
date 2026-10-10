from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..tracing import RunTrace


class CareerState(TypedDict):
    """Phase 1 only. Add actual business fields with their owning workflows later."""

    message: str
    counter: int
    result: str


def build_dummy_graph(trace: RunTrace):
    def prepare_input(state: CareerState):
        return {"message": state["message"].strip()}

    def mutate_state(state: CareerState):
        return {"counter": state["counter"] + 1, "result": f"Ready: {state['message']}"}

    graph = StateGraph(CareerState)
    graph.add_node("prepare_input", trace.node("prepare_input", prepare_input))
    graph.add_node("mutate_state", trace.node("mutate_state", mutate_state))
    graph.add_edge(START, "prepare_input")
    graph.add_edge("prepare_input", "mutate_state")
    graph.add_edge("mutate_state", END)
    return graph.compile()
