"""Real Chromium UI/capture tests. Synthetic media is injected by tests only."""
import json
import os
import threading
import time

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="Install .[dashboard-test] for browser tests")
from jev_factorio.dashboard import DashboardServer, EventWriter, Monitor


@pytest.fixture
def live(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path)
    writer.emit("run_started", 2, policy="hybrid", target="rocket_launch")
    monitor = Monitor(path)
    server = DashboardServer(0, monitor)
    follow = threading.Thread(target=monitor.follow, daemon=True)
    web = threading.Thread(target=server.serve_forever, daemon=True)
    follow.start(); web.start()
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM_PATH"),
                                    args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1672, "height": 1150}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield page, writer, f"http://127.0.0.1:{server.server_port}", errors
        context.close(); browser.close()
    writer.__exit__(None, None, None)
    monitor.stop.set(); server.shutdown(); server.server_close()
    follow.join(timeout=3); web.join(timeout=3)


def seed(writer):
    writer.emit("observation", 2, state={"session_id": "mock:browser-test", "world_kind": "mock", "tick": 314,
                "inventory": {"iron-ore": 148, "copper-ore": 82, "coal": 45, "iron-plate": 64},
                "player_position": [12, 0], "drill_status": "working", "drill_fuel": 5,
                "drill_output_connected": True, "iron_ore_collected": 8})
    writer.emit("goals", 3, goal="bootstrap_mining", target="rocket_launch", completed_goals={"stockpile_fuel": 100}, status="running")
    plan = {"id": "coal-buffer", "description": "Replenish the coal buffer", "steps": [{"action": "mine_coal"}]}
    other = {"id": "walk-coal", "description": "Approach observed coal", "steps": [{"action": "walk_to_coal"}]}
    writer.emit("model_request", 5, candidates={plan["id"]: plan, other["id"]: other}, questions={"candidate": {"type": "choice"}})
    writer.emit("model_started", 5)
    return plan


CAPTURE_FIXTURE = """(() => {
  window.testCapture = null;
  const media = () => {
    const canvas = document.createElement('canvas'); canvas.width = 960; canvas.height = 540;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#102a32'; ctx.fillRect(0,0,960,540);
    ctx.fillStyle = '#255b63';
    for (let x=0;x<960;x+=60) for(let y=0;y<540;y+=60) if((x+y)%120===0) ctx.fillRect(x,y,58,58);
    ctx.fillStyle = '#06161c'; ctx.fillRect(140,205,680,120);
    ctx.fillStyle = '#acf5e4'; ctx.font = '22px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('SYNTHETIC BROWSER CAPTURE TEST',480,255);
    ctx.font = '15px sans-serif'; ctx.fillText('Actual video element • not Factorio gameplay',480,290);
    const stream = canvas.captureStream(10); window.testCapture = stream;
    return Promise.resolve(stream);
  };
  Object.defineProperty(navigator.mediaDevices, 'getDisplayMedia', {value: media});
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {value: media});
  Object.defineProperty(navigator.mediaDevices, 'enumerateDevices', {value: async () => [{kind:'videoinput',deviceId:'test',label:'OBS Virtual Camera (test fixture)'}]});
})();"""


def test_live_decision_capture_freeze_inspector_and_overlay(live, tmp_path):
    page, writer, url, errors = live
    page.add_init_script(CAPTURE_FIXTURE)
    requests = []
    page.on("request", lambda req: requests.append(req.url))
    page.goto(url)
    plan = seed(writer)
    playwright.expect(page.locator("#thinking-status")).to_have_text("JEV is evaluating")
    playwright.expect(page.locator("#candidate-count")).to_have_text("2 MODEL CANDIDATES")
    page.locator("#capture-main").click()
    page.wait_for_function("document.querySelector('#game-video').videoWidth === 960")
    playwright.expect(page.locator("#video-status")).to_have_text("WINDOW CAPTURE")
    page.locator("#inspect-model").click()
    playwright.expect(page.locator("#inspector-content")).to_contain_text("coal-buffer")
    page.locator("#close-inspector").click()
    page.locator("#freeze").click()
    writer.emit("model_returned", 5, duration_ms=81)
    writer.emit("controller_state", 2, plan=plan, pending={"action": "mine_coal", "dispatch": "returned", "polls": 1},
                decision={"plan_id": "coal-buffer", "source": "jev", "utilities": {"coal-buffer": 0.83}})
    page.wait_for_timeout(800)
    playwright.expect(page.locator("#source-mode")).to_have_text("DISPLAY FROZEN")
    page.locator("#freeze").click()
    playwright.expect(page.locator("#selected-plan")).to_have_text("coal-buffer")
    playwright.expect(page.locator("#verification-status")).to_contain_text("Pending")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    screenshot = os.environ.get("DASHBOARD_SCREENSHOT")
    if screenshot:
        page.screenshot(path=screenshot, full_page=True)
    page.locator("#broadcast").click()
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgba(0, 0, 0, 0)"
    page.keyboard.press("b")
    page.locator("#stop-capture").click()
    assert page.evaluate("window.testCapture.getVideoTracks()[0].readyState") == "ended"
    playwright.expect(page.locator("#capture-placeholder")).to_be_visible()
    assert all(req.startswith(url) for req in requests)
    assert not errors


def test_camera_permission_denial_and_mobile_layout(live):
    page, writer, url, errors = live
    page.add_init_script("Object.defineProperty(navigator.mediaDevices,'getDisplayMedia',{value:()=>Promise.reject(new DOMException('denied','NotAllowedError'))});")
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(url)
    seed(writer)
    page.locator("#capture-main").click()
    page.wait_for_timeout(800)
    playwright.expect(page.locator("#notice")).to_contain_text("cancelled or denied")
    assert page.evaluate("document.querySelector('#game-video').srcObject === null")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors


def test_obs_camera_selection_and_stop(live):
    page, writer, url, errors = live
    page.add_init_script(CAPTURE_FIXTURE)
    page.goto(url)
    page.locator("#camera").click()
    playwright.expect(page.locator("#camera-devices")).to_be_visible()
    playwright.expect(page.locator("#camera-devices")).to_contain_text("OBS Virtual Camera")
    page.wait_for_function("document.querySelector('#game-video').videoWidth === 960")
    page.locator("#stop-capture").click()
    assert page.evaluate("window.testCapture.getVideoTracks()[0].readyState") == "ended"
    assert not errors


def test_untrusted_text_is_inert_and_export_is_display_only(live, tmp_path):
    page, writer, url, errors = live
    page.goto(url)
    seed(writer)
    writer.emit("controller_state", 2, plan={"id": "<img src=x onerror=alert(1)>", "steps": []}, status="running")
    playwright.expect(page.locator("#selected-plan")).to_contain_text("<img")
    assert page.locator("#selected-plan img").count() == 0
    with page.expect_download() as download:
        page.locator("#export").click()
    path = tmp_path / "export.json"
    download.value.save_as(path)
    assert json.loads(path.read_text())["complete_audit"] is False
    assert not errors
