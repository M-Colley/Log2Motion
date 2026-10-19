"""Smoke test a disposable bridge instance, including its real simulator."""
import asyncio
import json
import sys

from websockets.asyncio.client import connect


async def main(uri):
    for attempt in range(45):
        try:
            socket = await connect(uri, max_size=20 * 1024 * 1024)
            break
        except OSError:
            await asyncio.sleep(1)
    else:
        raise RuntimeError("Bridge did not start within 45 seconds")
    async with socket:
        async def request(payload):
            await socket.send(json.dumps(payload))
            return json.loads(await asyncio.wait_for(socket.recv(), timeout=120))

        session = await request({"type": "ADD_SESSION", "name": "Container smoke test"})
        assert session["type"] == "SESSION_ADDED", session
        node = {"id": "smoke:button", "name": "Tap", "type": "RECTANGLE",
                "normalizedCenter": [0.5, 0.5], "x": 10, "y": 10,
                "width": 40, "height": 40}
        await socket.send(json.dumps({"type": "START_SIM", "visualization": False,
                                      "data": {"nodes": [node, node]}}))
        progress = []
        while True:
            response = json.loads(await asyncio.wait_for(socket.recv(), timeout=120))
            if response["type"] == "SIM_PROGRESS":
                progress.append(response["current"])
            else:
                break
        assert response["type"] == "RESULT", response
        assert progress == [1, 2], progress
        episodes = response["data"]["episodes"]
        assert len(episodes) == 2
        assert all(len(ep["data"]["trajectory"]) > 1 for ep in episodes)
        assert all(ep["data"]["contact_indices"] for ep in episodes)
        pdf = await request({"type": "DOWNLOAD_SINGLE_RUN_PDF", "sessionId": session["session_id"],
                             "runId": response["data"]["run_id"]})
        assert pdf["type"] == "SINGLE_RUN_PDF", pdf
        print("Bridge smoke test passed: two physical taps, progress, history, PDF")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8080"))
