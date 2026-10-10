"""Check running local FastAPI + Vite servers; never calls a model provider."""

import argparse

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--frontend", default="http://127.0.0.1:5173")
    parser.add_argument(
        "--profile", action="store_true", help="Exercise Profile on an empty test DB"
    )
    parser.add_argument("--jobs", action="store_true", help="Exercise Jobs on an empty test DB")
    args = parser.parse_args()
    with (
        httpx.Client(base_url=args.backend, timeout=10) as backend,
        httpx.Client(base_url=args.frontend, timeout=10) as frontend,
    ):
        health = backend.get("/api/health")
        health.raise_for_status()
        assert health.json() == {"status": "ok", "phase": 3}
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
        if args.profile or args.jobs:
            assert backend.get("/api/profile").json()["profile"] is None, (
                "Use an empty temporary DB"
            )

            def post(path, payload):
                result = frontend.post(path, json=payload)
                result.raise_for_status()
                return result.json()

            raw = "使用 Python。"
            saved = post("/api/profile/raw", {"raw_input": raw, "expected_version": 0})["profile"]
            result = post("/api/profile/normalize", {"expected_version": saved["profile_version"]})
            draft = result["profile"]
            assert not draft["confirmed"] and draft["raw_input"] == raw
            nodes = backend.get(f"/api/debug/runs/{result['run_id']}").json()["nodes"]
            assert len(nodes) == 6
            assert nodes[1]["ai"]["prompt_version"] == "profile-facts-v1"
            assert nodes[3]["ai"]["prompt_version"] == "profile-normalize-v1"
            assert all("debug_payload" not in n for n in nodes)
            edited = {
                "entries": [
                    dict(
                        draft["effective_profile"]["entries"][0],
                        content="使用 Python 编写 synthetic 示例脚本。",
                    )
                ]
            }
            response = frontend.put("/api/profile", json={"expected_version": 2, "profile": edited})
            response.raise_for_status()
            assert response.json()["profile"]["raw_input"] == raw
            confirmed = post("/api/profile/confirm", {"expected_version": 3})["profile"]
            assert confirmed["confirmed"] and len(confirmed["evidence"]) == 1
            assert post("/api/profile/confirm", {"expected_version": 4})["profile"] == confirmed
            assert backend.get("/api/profile").json()["profile"] == confirmed
            fact = {"type": "skill", "title": "Python", "content": raw, "source_quote": raw}
            before = backend.get("/api/debug/runs").json()
            for task, payload in [
                ("profile-facts-v1", {"raw_input": raw}),
                ("profile-normalize-v1", {"facts": [fact]}),
            ]:
                replay = post("/api/debug/replay", {"task": task, "payload": payload})
                assert replay["metadata"]["provider"] == "replay"
            assert backend.get("/api/debug/runs").json() == before
            assert backend.get("/api/profile").json()["profile"] == confirmed
        if args.jobs:
            assert backend.get("/api/jobs").json() == {"jobs": []}
            result = post("/api/jobs/analyze", {"raw_jd": "Synthetic Python developer JD"})
            job = result["job"]
            assert job["coverage"] == 66.7 and not job["outdated"]
            assert len(job["requirements"]) == 4
            assert job["requirements"][0]["match"]["evidence"][0]["normalized_content"]
            assert backend.get(f"/api/jobs/{job['id']}").json()["job"] == job
            nodes = backend.get(f"/api/debug/runs/{result['run_id']}").json()["nodes"]
            assert len(nodes) == 8
            assert nodes[1]["ai"]["prompt_version"] == "jd-extract-v1"
            assert nodes[4]["ai"]["prompt_version"] == "matching-v1"
            before = backend.get("/api/debug/runs").json()
            for task, payload in [
                ("jd-extract-v1", {"raw_jd": "Synthetic Python developer. Python required."}),
                (
                    "matching-v1",
                    {
                        "requirements": [
                            {
                                "requirement_index": 0,
                                "text": "Python",
                                "importance": "required",
                                "category": "skill",
                                "scoreability": "SCORED",
                            }
                        ],
                        "evidence": [
                            {
                                "id": "synthetic-python",
                                "type": "skill",
                                "title": "Python",
                                "normalized_content": "Python",
                            }
                        ],
                    },
                ),
            ]:
                assert (
                    post("/api/debug/replay", {"task": task, "payload": payload})["metadata"][
                        "provider"
                    ]
                    == "replay"
                )
            assert backend.get("/api/debug/runs").json() == before
            assert backend.get("/api/jobs").json()["jobs"] == [job]
            post(
                "/api/profile/raw",
                {"raw_input": "Changed synthetic profile.", "expected_version": 4},
            )
            assert backend.get(f"/api/jobs/{job['id']}").json()["job"]["outdated"]
            assert (
                frontend.post("/api/jobs/analyze", json={"raw_jd": "Synthetic"}).status_code == 422
            )
    print("PASS: HTTP → Vite proxy → FastAPI → LangGraph → SQLite → trace response")
    print("PASS: debug UI shell / node diff / standalone replay; 0 live API calls")
    if args.jobs:
        print("PASS: Jobs analysis / coverage / evidence / eight traces / replays / outdated gate")
    if args.profile or args.jobs:
        print(
            "PASS: Profile raw / normalize / edit / atomic confirm / stable evidence / task replays"
        )


if __name__ == "__main__":
    main()
