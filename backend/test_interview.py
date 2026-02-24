"""Full end-to-end text interview test with REAL Gemini AI."""
import requests, sys, time

BASE = "http://127.0.0.1:8000"

def main():
    print("=" * 70)
    print("  REAL AI INTERVIEW TEST (gemini-2.5-flash)")
    print("=" * 70)

    r = requests.post(f"{BASE}/api/interview/sessions/create/", json={
        "candidate_name": "Vansh",
        "role": "AI Algorithm Engineer Intern",
    })
    assert r.status_code == 201, f"Create failed: {r.status_code} {r.text}"
    sid = r.json()["session_id"]
    print(f"\nSession: {sid}\n")

    turns = [
        ("start", ""),
        ("user_turn", "I am Vansh, a Masters student in AI at University at Buffalo. Im in my second semester studying NLP and reinforcement learning."),
        ("user_turn", "what"),
        ("user_turn", "I was drawn to this role because I want to apply deep learning and transformers in real-world products."),
        ("user_turn", "My strongest skills are Python, PyTorch, and transformer architectures. I also have experience with data pipelines and MLOps."),
        ("user_turn", "What excites me most is the intersection of NLP and real-time systems. I built a real-time sentiment analysis tool for social media."),
        ("user_turn", "For the sentiment project I was lead developer. I used fine-tuned DistilBERT deployed on AWS with Docker and FastAPI."),
        ("user_turn", "We achieved 92 percent accuracy and handled 3000 tweets per second with p99 latency under 180ms."),
    ]

    all_responses = []
    FALLBACKS = ["Hi Vansh! Welcome", "Thanks for joining", "That's great. What would",
                 "Interesting background!", "Nice! Let's dive", "Tell me more about the technical",
                 "What was the biggest challenge", "What impact did your work", "I'd love to hear"]

    for i, (evt, txt) in enumerate(turns):
        payload = {"session_id": sid, "event_type": evt}
        if txt: payload["user_text"] = txt
        t0 = time.time()
        r = requests.post(f"{BASE}/api/interview/ui/next_turn/", json=payload)
        ms = int((time.time() - t0) * 1000)
        assert r.status_code == 200, f"Turn {i} FAILED: {r.status_code}\n{r.text[:200]}"
        d = r.json()
        text, stage = d["assistant_text"], d["stage"]
        all_responses.append(text)
        src = "FALLBACK" if any(text.startswith(f) for f in FALLBACKS) else "AI"
        if txt: print(f"  YOU: {txt[:80]}...")
        print(f"  TAYLOR [{src}]: {text}")
        print(f"  [{stage}] {ms}ms\n")

    ai = sum(1 for t in all_responses if not any(t.startswith(f) for f in FALLBACKS))
    dupes = len(all_responses) - len(set(t[:40] for t in all_responses))
    combined = " ".join(all_responses).lower()
    refs = [w for w in ["vansh","buffalo","nlp","pytorch","sentiment","distilbert","transformer"] if w in combined]
    print("=" * 70)
    print(f"  AI: {ai}/{len(all_responses)} | Dupes: {dupes} | Refs: {refs}")
    print(f"  RESULT: {'PASS' if ai >= 4 and dupes == 0 else 'FAIL'}")

if __name__ == "__main__":
    main()
