"""Terminal chat, handy for quick checks.   python cli.py   (add --trace to see routing)"""
import sys
import uuid

from dotenv import load_dotenv

from advisor import llm as llm_mod
from advisor.graph import build, run_turn
from advisor.sop_store import load_policy

load_dotenv()

if __name__ == "__main__":
    trace = "--trace" in sys.argv
    llm = llm_mod.from_env()
    if llm is None:
        sys.exit("GROQ_API_KEY is not set. Add it to .env and run again.")
    graph = build(policy=load_policy, llm=llm)
    thread = str(uuid.uuid4())
    print("Weather advisor. Ctrl+C to quit, /new for a fresh session.\n")
    while True:
        try:
            q = input("you> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not q:
            continue
        if q == "/new":
            thread = str(uuid.uuid4())
            print("(new session)\n")
            continue
        out = run_turn(graph, thread, q)
        print("\n" + out["reply"] + "\n")
        if trace:
            print(f"  [{out['outcome']}] {' -> '.join(out['path'])}  {out.get('problems') or ''}\n")
