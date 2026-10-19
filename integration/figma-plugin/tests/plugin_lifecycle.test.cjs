const {test}=require("node:test");
const assert=require("node:assert/strict");
const {plugin}=require("./plugin_harness.cjs");

test("an asynchronous export keeps the click path it started with",async()=>{
  const app=plugin();const a=app.add("A");app.add("B");app.add("C");
  let resolveA;
  app.figma.getNodeByIdAsync=(id)=>id==="A"?new Promise((resolve)=>{resolveA=resolve;}):Promise.resolve(app.nodes.get(id));
  const pending=app.run('clickPath.push({id:"A",name:"A"},{id:"B",name:"B"});collectSelectedNodes()');
  app.run('clickPath.splice(1,1,{id:"C",name:"C"})');resolveA(a);
  assert.deepEqual(Array.from(await pending,(node)=>node.id),["A","B"]);
});

test("exports abort if a selected element vanished instead of silently changing click count",async()=>{
  const app=plugin();app.add("A");
  await assert.rejects(app.run('clickPath.push({id:"A",name:"A"},{id:"missing",name:"missing"});collectSelectedNodes()'),/missing|no longer/i);
});

test("highlight overlays do not change an auto-layout container",async()=>{
  const app=plugin();const node=app.add("A");let layoutMutations=0;
  node.parent.appendChild=()=>{layoutMutations++;node.x+=20;};
  app.run("selectionMode=true");
  await app.figma.ui.onmessage({type:"SELECT_NODE_CARD",id:"A"});
  assert.equal(layoutMutations,0);assert.equal(node.x,10);
  assert.equal(app.created[0].parent,app.page);
  assert.deepEqual(Array.from(app.created[0].relativeTransform[0]),[1,0,505]);
});

test("a pending highlight cannot reappear after selection is finished",async()=>{
  const app=plugin();const node=app.add("A");let resolveNode;
  app.figma.getNodeByIdAsync=()=>new Promise((resolve)=>{resolveNode=resolve;});
  app.run("selectionMode=true");
  const pending=app.figma.ui.onmessage({type:"SELECT_NODE_CARD",id:"A"});
  await app.figma.ui.onmessage({type:"FINISH_SELECTION"});resolveNode(node);await pending;
  assert.equal(app.created.filter((item)=>!item.removed).length,0);
});

test("ordinary document creation never suppresses the user's next click",async()=>{
  const app=plugin();app.add("created-user-node");const selected=app.add("A");await app.tick();
  app.run("selectionMode=true;showArrowsGlobally=false");
  await app.events.documentchange({documentChanges:[{type:"CREATE",id:"created-user-node"}]});
  app.page.selection=[selected];app.events.selectionchange();
  assert.deepEqual(Array.from(app.run("clickPath.map(node=>node.id)")),["A"]);
});

test("a connector still building its network is not mistaken for an undone orphan",async()=>{
  const app=plugin();app.add("A");app.add("B",60);await app.tick();
  const originalCreate=app.figma.createVector;let resolveNetwork;
  app.figma.createVector=()=>{const vector=originalCreate();vector.setVectorNetworkAsync=()=>new Promise((resolve)=>{resolveNetwork=resolve;});return vector;};
  const pending=app.run('selectionMode=true;clickPath.push({id:"A",name:"A"},{id:"B",name:"B"});drawVectors()');
  await app.tick();const vector=app.created[0];assert.ok(vector);
  await app.events.documentchange({documentChanges:[{type:"CREATE",id:vector.id}]});
  await app.flushTimers(50);assert.equal(vector.removed,false);
  resolveNetwork();await pending;
});

test("finishing selection removes a pending connector and its completion stays cancelled",async()=>{
  const app=plugin();app.add("A");app.add("B",60);
  const originalCreate=app.figma.createVector;let resolveNetwork;
  app.figma.createVector=()=>{const vector=originalCreate();vector.setVectorNetworkAsync=()=>new Promise((resolve)=>{resolveNetwork=resolve;});return vector;};
  const pending=app.run('selectionMode=true;clickPath.push({id:"A",name:"A"},{id:"B",name:"B"});drawVectors()');
  await app.tick();await app.figma.ui.onmessage({type:"FINISH_SELECTION"});
  resolveNetwork();await pending;
  assert.equal(app.created[0].removed,true);
  assert.equal(app.run("activeConnectors.length"),0);
});

test("exported geometry is captured before asynchronous thumbnail generation",async()=>{
  const app=plugin();const node=app.add("A");let resolveThumbnail;
  node.exportAsync=()=>new Promise((resolve)=>{resolveThumbnail=resolve;});
  const pending=app.run('clickPath.push({id:"A",name:"A"});collectSelectedNodes()');
  await app.tick();node.x=60;node.absoluteTransform=[[1,0,560],[0,1,320]];
  resolveThumbnail(new Uint8Array());const exported=(await pending)[0];
  assert.equal(exported.x,10);
  assert.deepEqual(Array.from(exported.normalizedCenter),[0.2,0.2]);
});
