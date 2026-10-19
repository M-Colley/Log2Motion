const fs = require("node:fs");
const vm = require("node:vm");
const { transformSync } = require("esbuild");

function plugin() {
  const nodes = new Map();
  const events = {};
  const created = [];
  const timers = [];
  const messages = [];
  const page = {id:"page",type:"PAGE",children:[],appendChild(node) { this.children.push(node); node.parent=this; },
    findAll: (predicate) => created.filter((node) => !node.removed && predicate(node))};
  function create(type) {
    const data = new Map();
    const node = {id:`created-${created.length}`,type,parent:page,removed:false,
      getPluginData: (key) => data.get(key) || "", setPluginData: (key, value) => data.set(key, value),
      resize(width,height) { this.width=width; this.height=height; },
      remove() { this.removed=true; }, setVectorNetworkAsync: async () => {}};
    created.push(node); nodes.set(node.id,node); return node;
  }
  const figma = {showUI() {}, root:{name:"Fixture"},currentPage:page,
    ui:{postMessage:(message)=>messages.push(JSON.parse(JSON.stringify(message)))},
    loadAllPagesAsync:async()=>{},on:(type,listener)=>{events[type]=listener;},
    getNodeByIdAsync:async(id)=>nodes.get(id),base64Encode:()=>"fixture",
    createRectangle:()=>create("RECTANGLE"),createVector:()=>create("VECTOR"),
    viewport:{scrollAndZoomIntoView(){}},setCurrentPageAsync:async(target)=>{figma.currentPage=target;}};
  const context = vm.createContext({figma,__html__:"",console,
    setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;}});
  vm.runInContext(transformSync(fs.readFileSync("code.ts","utf8"),{loader:"ts",target:"es2020"}).code,context);
  function run(code) { return vm.runInContext(code,context); }
  async function tick() { for(let index=0;index<10;index++) await Promise.resolve(); }
  async function flushTimers(maxMs=Infinity) {
    for(const timer of timers.splice(0)) { if(timer.ms<=maxMs) await timer.fn(); else timers.push(timer); }
    await tick();
  }
  function add(id,x=10) {
    const frame={id:`frame-${id}`,type:"FRAME",width:100,height:200,parent:page,absoluteTransform:[[1,0,500],[0,1,300]],
      appendChild(child) {child.parent=this;}};
    const node={id,name:id,type:"RECTANGLE",x,y:20,width:20,height:40,parent:frame,
      absoluteTransform:[[1,0,500+x],[0,1,320]],absoluteBoundingBox:{x:500+x,y:320,width:20,height:40},
      getPluginData:()=>"",exportAsync:async()=>new Uint8Array()};
    nodes.set(id,node);return node;
  }
  return {nodes,events,created,page,figma,messages,run,tick,flushTimers,add};
}

module.exports={plugin};
