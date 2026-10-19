const { test } = require("node:test");
const assert = require("node:assert/strict");
const { ui, simulation } = require("./ui_harness.cjs");

test("simulation failures restore navigation, visualization and paused animation", async () => {
  const app = ui();
  await app.document.getElementById("startBtn").dispatch("click");
  assert.equal(app.document.getElementById("toggle").disabled, true);
  app.sockets[0].receive({type:"SIM_ERROR", error:"Missing policy"});
  assert.equal(app.document.getElementById("startBtn").disabled, false);
  assert.equal(app.document.getElementById("toggle").disabled, false);
  assert.equal(app.document.getElementById("backBtn").classList.contains("hidden"), false);
  assert.equal(app.document.getElementById("lizardVideo").paused, true);
});

test("chart images stay with their run when the next result arrives before thumbnails", async () => {
  const app = ui();
  app.run('selectedElements.push({id:"A",name:"A",thumbnail:"pending-thumbnail"})');
  app.sockets[0].receive({type:"RESULT",data:simulation("1")});
  app.sockets[0].receive({type:"RESULT",data:simulation("2")});
  app.images[0].onload();
  assert.equal(app.charts[5].draws, 0, "An old thumbnail must not redraw the new run");
  await app.flushTimers();
  await app.flushTimers();
  const uploads = app.sockets[0].sent.filter((message) => message.type === "SAVE_SIMULATION_CHARTS");
  assert.equal(uploads.length, 2);
  assert.equal(uploads.find((message) => message.run_id === "1").charts.muscleCost, "image-1");
  assert.equal(uploads.find((message) => message.run_id === "2").charts.muscleCost, "image-4");
});

test("chart snapshot queued while disconnected is retried and matching acknowledgements clear it", async () => {
  const app = ui();
  app.sockets[0].receive({type:"RESULT",data:simulation("7","3")});
  app.sockets[0].readyState = 3;
  app.sockets[0].onclose();
  await app.flushTimers(2500);
  await app.flushTimers(2500);
  app.run("connectWebSocket()");
  const reconnected = app.sockets.at(-1);
  reconnected.onopen();
  const upload = reconnected.sent.find((message) => message.type === "SAVE_SIMULATION_CHARTS");
  assert.equal(upload?.session_id, "3");
  assert.equal(upload?.run_id, "7");
  reconnected.receive({type:"CHARTS_SAVED",session_id:"3",run_id:"7",history_data:[]});
  assert.equal(app.run("pendingChartUploads.size"), 0);
});

test("late diagram response cannot overwrite the newly selected run", () => {
  const app = ui();
  app.run('currentOverview={activeSessionId:"1",sessions:[]};currentHistoryRuns=[{id:"1",metrics:{}},{id:"2",metrics:{}}];selectedIndexSingle=1');
  app.sockets[0].receive({type:"SINGLE_RUN_DIAGRAMS",sessionId:"1",runId:"1",runIndex:0,images:{trajectory:"old"}});
  assert.equal(app.document.getElementById("chartsSectionSingle").innerHTML.includes("old"), false);
});

test("history requests carry explicit session and stale session responses are ignored", () => {
  const app = ui();
  app.run('currentOverview={activeSessionId:"2",sessions:[]};requestHistoryData()');
  assert.equal(app.sockets[0].sent.at(-1).session_id, "2");
  app.sockets[0].receive({type:"HISTORY_DATA",session_id:"1",data:[{id:"old-session"}]});
  assert.equal(app.run("currentHistoryRuns.length"), 0);
});

test("nonuniform sample times and exact durations position every chart point correctly", () => {
  const app = ui();
  const result = simulation();
  result.episodes[0].data = {times:[0,0.02,0.09],duration:0.09,dt:0.045,start_time:0,
    trajectory:[[0,0,0],[1,1,1],[2,2,2]],muscle_costs:[0.1,0.2,0.3],contact_indices:[1]};
  result.episodes.push({episode_id:"episode_1",data:{times:[0.005,0.02],duration:0.02,dt:0.015,start_time:0.005,
    trajectory:[[0,0,0],[1,1,1]],muscle_costs:[0.1,0.2],contact_indices:[1]}});
  app.sockets[0].receive({type:"RESULT",data:result});
  assert.deepEqual(Array.from(app.charts[0].data.datasets[0].data, (point) => point.x), [0,0.02,0.09]);
  assert.deepEqual(Array.from(app.charts[0].data.datasets[1].data, (point) => point.x), [0.095,0.11]);
  assert.equal(app.charts[2].data.datasets[1].data[0].x, 0.11);
  assert.equal(app.charts[0].options.scales.x.max, 0.11 * 1.02);
});

test("legacy uniform chart timing preserves start_time and has no extra trailing interval", () => {
  const app = ui();
  const timing = app.run('chartTiming({start_time:0.01,dt:0.01},3)');
  assert.deepEqual(Array.from(timing.sampleTimes), [0.01,0.02,0.03]);
  assert.equal(timing.duration, 0.03);
});

test("stale chart errors discard only their matching queued upload", async () => {
  const app = ui();
  app.sockets[0].receive({type:"RESULT",data:simulation("7","3")});
  await app.tick();
  assert.equal(app.run("pendingChartUploads.size"), 1);
  app.sockets[0].receive({type:"ERROR",request_type:"SAVE_SIMULATION_CHARTS",session_id:"3",run_id:"6",error:"Run removed"});
  assert.equal(app.run("pendingChartUploads.size"), 1);
  app.sockets[0].receive({type:"ERROR",request_type:"SAVE_SIMULATION_CHARTS",session_id:"3",run_id:"7",error:"Run removed"});
  assert.equal(app.run("pendingChartUploads.size"), 0);
});

test("pending simulation retains its starting session and visualization through reconnect", async () => {
  const app = ui();
  app.run('currentOverview={activeSessionId:"3",sessions:[]}');
  app.document.getElementById("toggle").checked = true;
  app.context.open = () => {};
  await app.document.getElementById("startBtn").dispatch("click");
  app.sockets[0].readyState = 3;
  await app.context.onmessage({data:{pluginMessage:{type:"SEND_JSON",payload:{nodes:[{id:"A",name:"Original"}]}}}});
  app.run('currentOverview.activeSessionId="9"');
  app.document.getElementById("toggle").checked = false;
  app.run("connectWebSocket()");
  app.sockets.at(-1).onopen();
  const request = app.sockets.at(-1).sent.find((message) => message.type === "START_SIM");
  assert.equal(request.session_id, "3");
  assert.equal(request.visualization, true);
});

test("export failures restore all simulation controls", async () => {
  const app = ui();
  await app.document.getElementById("startBtn").dispatch("click");
  await app.context.onmessage({data:{pluginMessage:{type:"EXPORT_ERROR",error:"Element removed"}}});
  assert.equal(app.document.getElementById("toggle").disabled, false);
  assert.equal(app.document.getElementById("backBtn").classList.contains("hidden"), false);
  assert.equal(app.document.getElementById("lizardVideo").paused, true);
});

test("disconnecting an active simulation restores controls without silently rerunning it", async () => {
  const app = ui();
  app.run('currentOverview={activeSessionId:"1",sessions:[]}');
  await app.document.getElementById("startBtn").dispatch("click");
  await app.context.onmessage({data:{pluginMessage:{type:"SEND_JSON",payload:{nodes:[{id:"A",name:"A"}]}}}});
  app.sockets[0].readyState = 3;
  app.sockets[0].onclose();
  assert.equal(app.document.getElementById("startBtn").disabled, false);
  assert.equal(app.document.getElementById("toggle").disabled, false);
  app.run("connectWebSocket()");
  app.sockets.at(-1).onopen();
  assert.equal(app.sockets.at(-1).sent.some((message) => message.type === "START_SIM"), false);
});

for (const legacy of [false, true]) {
  test(`comparison response cannot mix an old pair's charts with the current pair${legacy ? " on a legacy server" : ""}`, () => {
    const app = ui();
    app.run('currentOverview={activeSessionId:"1",sessions:[]};currentHistoryRuns=["A","B","C","D"].map(id=>({id,name:id,metrics:{}}));selectedIndexA=0;selectedIndexB=1;executeRunComparison(currentHistoryRuns[0],currentHistoryRuns[1],"1");selectedIndexA=2;selectedIndexB=3;executeRunComparison(currentHistoryRuns[2],currentHistoryRuns[3],"1")');
    const response = (runA, runB) => ({type:"COMPARISON_RESULT", data:{added_elements:[{id:runA,name:runA}]},
      ...(legacy ? {} : {session_id:"1",run_a_id:runA,run_b_id:runB})});
    app.sockets[0].receive(response("A", "B"));
    assert.equal(app.run("currentComparisonResult"), null);
    app.sockets[0].receive(response("C", "D"));
    assert.equal(app.run("currentComparisonResult.added_elements[0].id"), "C");
    assert.equal(app.document.getElementById("selectedElementsHeader1").textContent, "C");
    assert.equal(app.document.getElementById("selectedElementsHeader2").textContent, "D");
  });

  test(`comparison response is ignored after switching sessions${legacy ? " on a legacy server" : ""}`, () => {
    const app = ui();
    app.run('currentOverview={activeSessionId:"1",sessions:[]};currentHistoryRuns=[{id:"A",metrics:{}},{id:"B",metrics:{}}];selectedIndexA=0;selectedIndexB=1;executeRunComparison(currentHistoryRuns[0],currentHistoryRuns[1],"1");currentOverview.activeSessionId="2"');
    app.sockets[0].receive({type:"COMPARISON_RESULT",data:{added_elements:[{id:"old-session"}]},
      ...(legacy ? {} : {session_id:"1",run_a_id:"A",run_b_id:"B"})});
    assert.equal(app.run("currentComparisonResult"), null);
  });
}

test("retryable chart storage errors back off, retain snapshots and recover on reconnect without alert storms", async () => {
  const app = ui();
  const socket = app.sockets[0];
  const uploads = () => socket.sent.filter((message) => message.type === "SAVE_SIMULATION_CHARTS");
  const failure = {type:"ERROR",request_type:"SAVE_SIMULATION_CHARTS",session_id:"3",run_id:"8",
    retryable:true,error:"[Errno 28] No space left on device"};
  socket.receive({type:"RESULT",data:simulation("8","3")});
  await app.tick();
  assert.equal(uploads().length, 1);
  for (const [retry, delay] of [1000, 2000, 4000].entries()) {
    socket.receive(failure);
    socket.receive(failure); // A duplicate failure must not schedule another retry.
    assert.equal(app.run("pendingChartUploads.size"), 1);
    await app.flushTimers(delay - 1);
    assert.equal(uploads().length, retry + 1);
    await app.flushTimers(delay);
    assert.equal(uploads().length, retry + 2);
    assert.equal(app.alerts.length, 0);
  }
  socket.receive(failure);
  socket.receive(failure);
  await app.flushTimers();
  assert.equal(uploads().length, 4, "Retries must stop after the bounded backoff");
  assert.equal(app.alerts.length, 1);
  assert.equal(app.run("pendingChartUploads.size"), 1);
  socket.readyState = 3;
  socket.onclose();
  app.run("connectWebSocket()");
  const reconnected = app.sockets.at(-1);
  reconnected.onopen();
  assert.equal(reconnected.sent.filter((message) => message.type === "SAVE_SIMULATION_CHARTS").length, 1);
  reconnected.receive({type:"CHARTS_SAVED",session_id:"3",run_id:"8"});
  assert.equal(app.run("pendingChartUploads.size"), 0);
});

for (const response of [
  {type:"CHARTS_SAVED",session_id:"3",run_id:"8"},
  {type:"ERROR",request_type:"SAVE_SIMULATION_CHARTS",session_id:"3",run_id:"8",retryable:false,error:"Run removed"},
]) {
  test(`${response.type} clears a matching chart snapshot and cancels its pending retry`, async () => {
    const app = ui();
    const socket = app.sockets[0];
    socket.receive({type:"RESULT",data:simulation("8","3")});
    await app.tick();
    socket.receive({type:"ERROR",request_type:"SAVE_SIMULATION_CHARTS",session_id:"3",run_id:"8",retryable:true,error:"Temporary storage failure"});
    socket.receive(response);
    await app.flushTimers();
    assert.equal(app.run("pendingChartUploads.size"), 0);
    assert.equal(socket.sent.filter((message) => message.type === "SAVE_SIMULATION_CHARTS").length, 1);
  });
}
