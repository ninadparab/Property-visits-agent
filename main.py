# main.py

from dotenv import load_dotenv
load_dotenv()

from graph import agent


def run_agent(user_message: str) -> dict:
    inputs = {
        "messages":       [],
        "raw_input":      user_message,
        "open_houses":    [],
        "travel_matrix":  {},
        "schedules":      [],
        "unschedulable":  [],
        "error":          None,
        "next_step":      None,
    }
    return agent.invoke(inputs)


if __name__ == "__main__":

    result = run_agent(
        "Find open houses in Redmond WA this Saturday. "
        "I'll leave home at 9am from 500 108th Ave NE, Bellevue WA. "
        "Looking for 3+ bed houses or condos under $1.5M, built after 1980."
    )

    print("\nFinal state keys:", list(result.keys()))