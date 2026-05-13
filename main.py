# main.py

from dotenv import load_dotenv
load_dotenv()

from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver

from graph import build_graph

# Local runs need MemorySaver so interrupt() can persist state between pauses.
# Studio imports graph.py directly and supplies its own checkpointer.
agent = build_graph(checkpointer=MemorySaver())


def run_agent(user_message: str, thread_id: str = "1") -> dict:
    """
    Run the open house agent. Handles mid-flow interrupts (0 results → relax
    filters) and multi-turn feedback (user modifies schedule after seeing it).

    Each interrupt pauses the graph, prints a question, waits for user input,
    then resumes. Type 'done' at the feedback prompt to exit.
    """
    config = {"configurable": {"thread_id": thread_id}}

    inputs = {
        "messages":      [],
        "raw_input":     user_message,
        "open_houses":   [],
        "travel_matrix": {},
        "schedules":     [],
        "unschedulable": [],
        "error":         None,
        "next_step":     None,
    }

    _stream(inputs, config)

    # Loop until graph finishes (no pending nodes)
    while True:
        state = agent.get_state(config)
        if not state.next:
            break

        # Surface the interrupt question to the user
        for task in state.tasks:
            for itr in task.interrupts:
                print(f"\nAgent:\n{itr.value}")

        user_input = input("\nYou: ").strip()
        _stream(Command(resume=user_input), config)

    return agent.get_state(config).values


def _stream(inputs_or_cmd, config: dict) -> None:
    """Stream graph events and print assistant messages as they arrive."""
    for event in agent.stream(inputs_or_cmd, config, stream_mode="values"):
        messages = event.get("messages", [])
        if not messages:
            continue
        last = messages[-1]
        content = last.get("content") if isinstance(last, dict) else getattr(last, "content", None)
        role    = last.get("role")    if isinstance(last, dict) else getattr(last, "type",    None)
        if content and role in ("assistant", "ai"):
            print(f"\n{content}")


if __name__ == "__main__":
    print("Open House Visit Planner")
    print("Example: 'Find open houses in Redmond WA this Saturday. I'll leave at 9am from 500 108th Ave NE Bellevue WA. 3+ beds under $1.5M.'")
    print()
    user_message = input("You: ").strip()
    if user_message:
        run_agent(user_message)
