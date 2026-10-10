"""Check running local FastAPI + Vite servers; never calls a model provider."""

import argparse

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--frontend", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    with (
        httpx.Client(base_url=args.backend, timeout=10) as backend,
        httpx.Client(base_url=args.frontend, timeout=10) as frontend,
    ):
        health = backend.get("/api/health")
        health.raise_for_status()
        assert health.json() == {"status": "ok", "phase": 1}
        config = frontend.get("/api/settings")
        config.raise_for_status()
        assert config.json()["ai_mode"] == "fake", "Run smoke check with AI_MODE=fake"
        assert config.json()["debug_enabled"], "Run smoke check in development"
        page = frontend.get("/debug/runs")
        page.raise_for_status()
        assert '<div id="root"></div>' in page.text
        response = frontend.post("/api/workflows/dummy", json={"message": "HTTP smoke"})
        response.raise_for_status()
        body = response.json()
        assert body["state"] == {
            "message": "HTTP smoke",
            "counter": 1,
            "result": "Ready: HTTP smoke",
        }
        response = frontend.get(f"/api/debug/runs/{body['run_id']}")
        response.raise_for_status()
        run = response.json()
        assert run["status"] == "completed"
        assert [n["node_name"] for n in run["nodes"]] == ["prepare_input", "mutate_state"]
        assert run["nodes"][1]["state_changes"]["counter"] == {"before": 0, "after": 1}
        assert all(n["ai"] is None and "debug_payload" not in n for n in run["nodes"])
        before = backend.get("/api/debug/runs").json()
        response = frontend.post("/api/debug/replay", json={"task": "diagnostic", "payload": {}})
        response.raise_for_status()
        assert response.json()["metadata"]["provider"] == "replay"
        assert response.json()["metadata"]["total_tokens"] == 0
        assert backend.get("/api/debug/runs").json() == before
    print("PASS: HTTP → Vite proxy → FastAPI → LangGraph → SQLite → trace response")
    print("PASS: debug UI shell / node diff / standalone replay; 0 model calls")


if __name__ == "__main__":
    main()
