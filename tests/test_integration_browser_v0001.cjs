// ============================================================
// 文件名: test_integration_browser_v0001.cjs
// 中文名: SC-TSE Integration MVP 浏览器验收
// 版本号: v0001
//
// 主层级: action
// 层级: staging / sc_tse_integration_mvp / tests
// 脚本定位: 通过真实 HTTP 主链验证前端操作闭环
//
// 职责说明:
// - 验证载入、选中、手动坐标修改、运行和状态呈现
//
// 本脚本做什么:
// - 保存断言结果及真实浏览器截图
//
// 本脚本不做什么:
// - 不伪造成功计算，不修改活动项目数据
//
// 制度边界声明:
// - 截图与日志仅写显式 SC_TSE_TEST_OUTPUT
// - 离线测试标记为传输故障注入
//
// 可更新: True
// ============================================================

// ============================================================
// ALIAS_META
// ============================================================
// alias: test_integration_browser_v0001
// family: test_integration_browser
// role: integration_browser_acceptance
// version: v0001
// status: experimental
// entry_point: tests/test_integration_browser_v0001.cjs
// input:
//   - SC_TSE_TEST_URL and SC_TSE_TEST_OUTPUT
// output:
//   - screenshots and browser_results.json
// depends_on:
//   - Playwright
//   - run_sc_tse_integration_v0001
// used_by: []
// ============================================================

const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const DEFAULT_ENCODING='utf-8';
const SCRIPT_FAMILY='test_integration_browser';
const SCRIPT_NAME='test_integration_browser_v0001';
const SCRIPT_VERSION='v0001';
const URL=process.env.SC_TSE_TEST_URL || 'http://127.0.0.1:8770';
const OUTPUT=process.env.SC_TSE_TEST_OUTPUT;

// ============================================================
// CLI / main 接口区
// ============================================================

async function main(){
  assert(OUTPUT,'explicit output required');fs.mkdirSync(OUTPUT,{recursive:true});
  const browser=await chromium.launch({headless:true,channel:process.env.DEMO_BROWSER_CHANNEL || 'msedge'});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],checks=[];let runs=0;
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.url().endsWith('/api/run'))runs++;});
  const check=(name)=>checks.push({name,status:'PASS'});
  try{
    await page.goto(URL);await page.locator('#centerY').waitFor({state:'visible'});
    await page.waitForFunction(()=>document.getElementById('projectTitle').textContent.includes('四体块'));
    assert.equal(runs,0);assert.equal(await page.locator('.block-shape').count(),4);check('Project load displays explicit input without computing');
    await page.locator('#run').click();await page.waitForFunction(()=>document.getElementById('status').textContent==='COMPLETED',{},{timeout:120000});
    assert.equal(runs,1);assert.equal(await page.locator('#pipeline li.passed').count(),6);
    assert.equal(await page.locator('#accessStatus').textContent(),'PASS');assert((await page.locator('.road-line').count())>0);
    await page.screenshot({path:path.join(OUTPUT,'baseline.png'),fullPage:true});check('Single HTTP request runs all six stages and displays computed roads');
    await page.locator('.object-row',{hasText:'B3_ADMIN'}).click();
    await page.locator('#centerY').fill('135');assert.equal(runs,1);
    assert.equal(await page.locator('.road-line').count(),0);assert.equal(await page.locator('#accessStatus').textContent(),'—');check('Manual editing clears obsolete roads and does not compute');
    const movedReply=page.waitForResponse(r=>r.url().endsWith('/api/run'));
    await page.locator('#run').click();assert(await page.locator('#centerY').isDisabled());
    const moved=await (await movedReply).json();await page.waitForFunction(()=>document.getElementById('status').textContent==='COMPLETED',{},{timeout:120000});
    assert.equal(moved.scene.blocks.find(b=>b.block_id==='B3_ADMIN').geometry.center[1],135);
    assert.equal(moved.metrics.road_edges,545);assert.equal(moved.metrics.access_status,'PASS');
    assert.equal(await page.locator('#edgeCount').textContent(),'545');
    await page.screenshot({path:path.join(OUTPUT,'moved.png'),fullPage:true});check('Edited coordinates produce a new real RoadGraph / Phase4 / Access result');
    await page.locator('#centerY').fill('80');await page.locator('#run').click();
    await page.waitForFunction(()=>document.getElementById('status').textContent==='WAITING_FOR_EXTERNAL_UPDATE',{},{timeout:120000});
    assert((await page.locator('#blockers').textContent()).includes('layout'));
    assert((await page.locator('#blockers').textContent()).includes('HARD'));
    assert((await page.locator('#blockers').textContent()).includes('实际间距 0 米，要求至少 13 米'));
    assert.equal(await page.locator('.road-line').count(),0);assert.equal(await page.locator('#accessStatus').textContent(),'—');
    await page.screenshot({path:path.join(OUTPUT,'blocked.png'),fullPage:true});check('Real constraint failure shows blocker and no stale downstream result');
    await page.locator('#centerY').fill('135');await page.locator('#run').click();
    await page.waitForFunction(()=>document.getElementById('status').textContent==='COMPLETED',{},{timeout:120000});check('Explicit correction recovers through a fresh full run');
    await page.locator('#centerY').fill('');await page.locator('#run').click();assert.equal(runs,4);check('Empty coordinate does not send a compute request');
    await page.locator('#loadCase').click();await page.waitForFunction(()=>document.getElementById('status').textContent==='未运行');
    const project=(await (await page.request.get(URL+'/api/project')).json()).project;
    await page.locator('#projectFile').setInputFiles({name:'case.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(project))});
    await page.waitForFunction(()=>document.getElementById('status').textContent==='未运行');check('Versioned local JSON project load');
    const denied=await page.request.post(URL+'/api/run',{data:{}});assert.equal(denied.status(),403);
    const invalid=await page.request.post(URL+'/api/run',{data:{},headers:{'X-SC-TSE':'integration-v1'}});assert.equal(invalid.status(),400);assert.equal((await invalid.json()).status,'ERROR');check('HTTP input and same-origin header guards');
    await page.route('**/api/run',route=>route.abort('failed'));
    await page.locator('#run').click();await page.waitForFunction(()=>document.getElementById('status').textContent==='ERROR');
    assert.equal(await page.locator('.road-line').count(),0);check('Injected transport outage displays ERROR without fallback');
    await page.unroute('**/api/run');
    await page.setViewportSize({width:1100,height:800});await page.screenshot({path:path.join(OUTPUT,'compact.png'),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);check('Desktop compact layout has no horizontal overflow');
    assert.deepEqual(errors,[]);check('No browser runtime exceptions');
    fs.writeFileSync(path.join(OUTPUT,'browser_results.json'),JSON.stringify({checks,errors},null,2));
    console.log(JSON.stringify({status:'PASS',checks:checks.length,output:OUTPUT}));
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
