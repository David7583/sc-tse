// ============================================================
// 文件名: integration_app_v0001.js
// 中文名: SC-TSE Integration MVP 交互与事实展示
// 版本号: v0001
//
// 主层级: action
// 层级: development / sc_tse / frontend / presentation
// 脚本定位: 通过 JSON 接口载入项目、编辑坐标和显示主链结果
//
// 职责说明:
// - 管理用户输入、选择、请求生命周期与结果展示
//
// 本脚本做什么:
// - 绘制明确输入的体块和后端返回的道路中心线
//
// 本脚本不做什么:
// - 不运行求解算法、拖拽、自动计算或几何补全
//
// 制度边界声明:
// - 编辑即清除旧计算结果；只在点击运行时 POST
// - 文本通过 textContent 写入；不保存或覆盖源项目
//
// 可更新: True
// ============================================================

// ============================================================
// ALIAS_META
// ============================================================
// alias: integration_app_v0001
// family: integration_app
// role: integration_ui_controller
// version: v0001
// status: active
// entry_point: scripts/action/development/scripts/sc_tse/frontend/integration_app_v0001.js
// input:
//   - versioned project and result JSON
//   - explicit coordinate edits
// output:
//   - two-dimensional factual scene and workflow status
// depends_on:
//   - run_sc_tse_integration_v0001
// used_by: []
// ============================================================

"use strict";

const DEFAULT_ENCODING = "utf-8";
const SCRIPT_FAMILY = "integration_app";
const SCRIPT_NAME = "integration_app_v0001";
const SCRIPT_VERSION = "v0001";
const SVG_NS = "http://www.w3.org/2000/svg";
const $ = id => document.getElementById(id);
const STAGES = [["precheck", "Precheck"], ["layout", "Layout"], ["connectivity", "Connectivity"], ["road_graph", "RoadGraph"], ["engineering", "Phase4"], ["access", "Access"]];

// ============================================================
// 数据结构 / 工具函数区
// ============================================================

const state = {project: null, inputScene: null, edits: new Map(), selected: null, result: null, busy: false, generation: 0};

function text(id, value) { $(id).textContent = value; }
function svgElement(tag, attributes, label) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (label !== undefined) node.textContent = label;
  return node;
}
function numberLabel(value) { return value === null || value === undefined ? "—" : String(value); }
function points(coordinates) { return coordinates.map(([x, y]) => `${x},${-y}`).join(" "); }
function draftBlocks() {
  return state.project.case.blocks.map(block => {
    const result = structuredClone(block), edit = state.edits.get(block.block_id);
    if (edit) result.geometry.center = [edit.center_x, edit.center_y];
    return result;
  });
}
function setStatus(status, description, css) {
  text("status", status); $("status").className = `status ${css}`;
  text("statusDescription", description); text("footerStatus", description);
}
function controls() {
  for (const id of ["loadCase", "loadFile", "caseSelect"]) $(id).disabled = state.busy;
  for (const id of ["centerX", "centerY"]) $(id).disabled = state.busy || !state.selected;
  $("run").disabled = state.busy || !state.project;
  $("run").firstElementChild.textContent = state.busy ? "完整重算中…" : "重新运行";
  $("viewport").setAttribute("aria-busy", String(state.busy));
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-SC-TSE": "integration-v1"}, body: JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok || result.status === "ERROR") throw new Error(`${result.error_type || response.status}: ${result.detail || "请求失败"}`);
  return result;
}

// ============================================================
// 核心组件：画布、属性与结果展示
// ============================================================

function draw() {
  if (!state.project) return;
  const svg = $("site"), current = state.result?.scene;
  const site = (current || state.inputScene).site;
  const blocks = current?.blocks || draftBlocks();
  svg.replaceChildren();
  svg.append(svgElement("polygon", {points: points(site.coordinates), class: "site-boundary"}));
  for (const path of current?.road_graph?.polylines || []) {
    svg.append(svgElement("polyline", {points: points(path.coordinates), class: "graph-line"}));
  }
  for (const road of current?.engineered_roads || []) {
    svg.append(svgElement("polyline", {points: points(road.coordinates), class: `road-line ${road.admission_status === "ADMITTED" ? "" : "rejected"}`}));
  }
  for (const block of blocks) {
    const g = block.geometry, [x, y] = g.center;
    const rect = svgElement("rect", {x: x-g.width_m/2, y: -y-g.height_m/2, width: g.width_m, height: g.height_m,
      class: `block-shape ${block.block_id === state.selected ? "selected" : ""}`, "data-block-id": block.block_id,
      tabindex: 0, role: "button", "aria-label": `选择 ${block.block_id}`});
    rect.addEventListener("click", () => select(block.block_id));
    rect.addEventListener("keydown", event => {if (["Enter", " "].includes(event.key)) {event.preventDefault(); select(block.block_id);}});
    svg.append(rect, svgElement("text", {x, y: -y+1, class: "block-label"}, block.block_id));
  }
  // View transform only; no model coordinates or routes are inferred here.
  const xs = site.coordinates.map(p => p[0]), ys = site.coordinates.map(p => p[1]);
  for (const b of blocks) {xs.push(b.geometry.center[0]-b.geometry.width_m/2,b.geometry.center[0]+b.geometry.width_m/2);ys.push(b.geometry.center[1]-b.geometry.height_m/2,b.geometry.center[1]+b.geometry.height_m/2);}
  const minX=Math.min(...xs), maxX=Math.max(...xs), minY=Math.min(...ys), maxY=Math.max(...ys);
  const pad=Math.max(maxX-minX,maxY-minY)*.08 || 1;
  svg.setAttribute("viewBox", `${minX-pad} ${-maxY-pad} ${maxX-minX+pad*2} ${maxY-minY+pad*2}`);
}

function objectList() {
  $("objectList").replaceChildren();
  for (const block of state.project?.case.blocks || []) {
    const button=document.createElement("button"); button.className=`object-row ${block.block_id===state.selected ? "selected" : ""}`;
    button.textContent=block.block_id;button.disabled=state.busy;button.addEventListener("click",()=>select(block.block_id));
    $("objectList").append(button);
  }
}
function select(identity) {
  if (state.busy) return;
  state.selected=identity;
  const block=draftBlocks().find(b=>b.block_id===identity);
  text("selectedId",identity);text("selectedType",`${block.geometry.width_m} × ${block.geometry.height_m} m`);
  $("centerX").value=block.geometry.center[0];$("centerY").value=block.geometry.center[1];
  objectList();draw();controls();
}
function resultPanels() {
  $("pipeline").replaceChildren();
  for (const [identity,title] of STAGES) {
    const stage=state.result?.workflow.workflow_state.nodes[identity];
    const item=document.createElement("li"),heading=document.createElement("strong"),label=document.createElement("small");
    heading.textContent=title;label.textContent=stage?.status || "未运行";
    item.className=stage?.status==="PASSED" ? "passed" : stage?.status==="WAITING_EXTERNAL_UPDATE" ? "blocked" : "";
    item.dataset.node=identity;item.append(heading,label);$("pipeline").append(item);
  }
  const metrics=state.result?.metrics;
  text("edgeCount",numberLabel(metrics?.road_edges));text("cycleCount",numberLabel(metrics?.road_cycles));
  text("accessStatus",numberLabel(metrics?.access_status));text("elapsed",metrics ? `${(metrics.elapsed_ms/1000).toFixed(2)} s` : "—");
  $("blockers").replaceChildren();
  for (const blocker of state.result?.blocking_nodes || []) {
    const box=document.createElement("div"),title=document.createElement("strong"),description=document.createElement("span");box.className="blocker";
    title.textContent=`阻断节点 · ${blocker.node_id}`;
    const reasons=blocker.diagnostics?.detail || JSON.stringify(blocker.failure_data || blocker.gate || {});
    box.append(title);
    const evidence=state.result.results?.[blocker.node_id]?.evidence || blocker.feedback?.computational_result?.evidence || [];
    const distances=evidence.filter(item=>item.constraint_type==="min_distance" && item.satisfied===false);
    for (const item of distances) {
      const detail=document.createElement("p");
      const actual=item.observed?.distance_m, required=item.required?.minimum_distance_m;
      const actualLabel=Number.isFinite(actual) ? `${actual} 米` : "后端未提供";
      const requiredLabel=Number.isFinite(required) ? `至少 ${required} 米` : "后端未提供";
      detail.textContent=`${(item.subjects || []).join(" 与 ")}：实际间距 ${actualLabel}，要求${requiredLabel}。`;
      box.append(detail);
    }
    if (distances.length) {
      const note=document.createElement("p");
      note.textContent="间距校核未通过。请修改坐标后重新运行。";
      box.append(note);
      const raw=document.createElement("details"),summary=document.createElement("summary");
      summary.textContent="原始诊断";description.textContent=reasons;
      raw.append(summary,description);box.append(raw);
    } else {description.textContent=reasons;box.append(description);}
    $("blockers").append(box);
  }
  text("evidence",state.result ? JSON.stringify({request_id:state.result.request_id,input_hash:state.result.input_hash,result_hash:state.result.result_hash,
    executed_node_ids:state.result.workflow.executed_node_ids,blocking_nodes:state.result.blocking_nodes,results:state.result.results},null,2) : "尚无当前输入的计算结果");
}
function invalidate() {
  state.result=null;state.generation++;
  setStatus("待重新运行","坐标已修改。旧道路与校核结果已清除，等待完整重算。","idle");
  text("geometrySource","明确输入 · 待重算");text("canvasNote","当前为编辑后的输入位置，尚未通过本次校核。");
  resultPanels();draw();
}
function editCoordinates() {
  if (!state.selected || state.busy) return;
  invalidate();
  if (!$("coordinateForm").checkValidity()) return;
  state.edits.set(state.selected,{block_id:state.selected,center_x:Number($("centerX").value),center_y:Number($("centerY").value)});
  draw();
}

// ============================================================
// 请求生命周期 / main 接口区
// ============================================================

async function loadProject(project) {
  if (state.busy) return;
  state.busy=true;controls();
  try {
    const loaded=await api("/api/project",project);
    state.project=loaded.project;state.inputScene=loaded.scene;state.edits.clear();state.result=null;state.selected=null;state.generation++;
    text("projectTitle",state.project.title);text("sourceNote",state.project.provenance.synthetic ? "合成验证案例 · 非测量数据" : "用户载入的结构化项目");
    text("geometrySource","明确输入 · 未运行");text("canvasNote","当前为项目输入。点击重新运行，生成本次道路与校核结果。");
    setStatus("未运行","项目已载入。选择体块或直接执行完整主链。","idle");
    resultPanels();
  } catch(error) {state.result=null;resultPanels();draw();setStatus("ERROR",error.message,"error");text("geometrySource","明确输入 · 载入失败");text("canvasNote","新项目未载入，保留原项目输入；计算结果已清除。");}
  finally {state.busy=false;controls();if(state.project)select(state.project.case.blocks[0].block_id);}
}
async function run() {
  if (state.busy || !state.project || !$("coordinateForm").reportValidity()) return;
  state.busy=true;state.result=null;const generation=++state.generation;
  controls();objectList();resultPanels();draw();setStatus("运行中","正在执行完整主链，请等待计算完成。","running");
  text("geometrySource","明确输入 · 计算中");text("canvasNote","本次计算完成前，不显示旧道路结果。");
  const requestId=crypto.randomUUID();
  try {
    const result=await api("/api/run",{schema_version:"sc-tse-integration-request-v0001",request_id:requestId,project:state.project,edits:[...state.edits.values()]});
    if(generation!==state.generation || result.request_id!==requestId)throw new Error("请求与返回结果不匹配，结果未采用。");
    state.result=result;
    const success=result.status==="COMPLETED";
    setStatus(result.status,success ? "完整主链通过。布局、道路与接入结果已更新。" : "主链被阻断。请查看失败原因，修改输入后重新运行。",success ? "success" : "waiting");
    text("geometrySource",result.scene.layout_source==="COMPUTED_LAYOUT" ? "本次计算结果" : "明确输入 · 布局未通过");
    text("canvasNote",success ? "道路中心线来自本次 RoadGraph / Phase4 计算。" : "仅展示本次已产生的事实；未执行节点不补结果。");
    resultPanels();draw();
  } catch(error) {state.result=null;resultPanels();draw();setStatus("ERROR",error.message,"error");text("geometrySource","明确输入 · 计算失败");text("canvasNote","没有可用的本次计算结果。");}
  finally {state.busy=false;controls();objectList();}
}
function main() {
  $("loadCase").addEventListener("click",()=>loadProject());
  $("loadFile").addEventListener("click",()=>$("projectFile").click());
  $("projectFile").addEventListener("change",async event=>{
    const file=event.target.files[0];if(!file)return;
    try {if(file.size>2097152)throw new Error("项目文件不能超过 2 MiB");await loadProject(JSON.parse(await file.text()));}
    catch(error){state.result=null;resultPanels();draw();setStatus("ERROR",error.message,"error");text("geometrySource","明确输入 · 载入失败");text("canvasNote","项目文件无效。没有采用新输入或旧计算结果。");}
    finally {event.target.value="";}
  });
  $("centerX").addEventListener("input",editCoordinates);$("centerY").addEventListener("input",editCoordinates);
  $("coordinateForm").addEventListener("submit",event=>event.preventDefault());
  $("run").addEventListener("click",run);$("fit").addEventListener("click",draw);
  resultPanels();loadProject();
}
main();
