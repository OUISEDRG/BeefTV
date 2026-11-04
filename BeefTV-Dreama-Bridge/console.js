'use strict';
let token=location.hash.slice(1)||sessionStorage.getItem('dreama-local-key')||'';
history.replaceState(null,'',location.pathname);
if(token) sessionStorage.setItem('dreama-local-key',token);
const labels={preparing:'准备输入素材',prepared:'准备就绪',submitting:'正在提交',queued:'排队中',processing:'处理中',submission_unknown:'回执待核对，不会重复提交',completed:'已完成',failed:'失败',cancelled:'已取消',unknown:'预检待核对',download_failed:'正在重试取回结果'};
const $=id=>document.getElementById(id);
let busy=false, refreshing=false, catalog={}, policy=null, serverOffset=0;
const countdowns=new Map();
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});const j=await r.json();if(!r.ok)throw Error(j.error?.message||'请求失败');return j;}
function text(parent,tag,value,className){const e=document.createElement(tag);e.textContent=value;if(className)e.className=className;parent.append(e);return e;}
function updateCountdowns(){for(const {job,line,execute} of countdowns.values()){
 const attempting=job.autoAttemptedQuoteId===job.quoteId&&!job.autoExecuteError;
 execute.disabled=attempting;
 if(job.autoExecuteError&&job.autoExecuteQuoteId===job.quoteId){line.textContent='自动执行已暂停：'+job.autoExecuteError;continue;}
 if(attempting){line.textContent='后台正在核对并提交任务。';continue;}
 if(job.autoExecuteAt&&job.autoExecuteQuoteId===job.quoteId){
  const seconds=Math.max(0,Math.ceil(job.autoExecuteAt-(Date.now()/1000+serverOffset)));
  line.textContent=seconds?`${seconds} 秒后自动执行 · 预计 ${job.pointsEstimate} 积分`:'倒计时结束，等待后台执行。';
 }else if(policy?.mode==='background-countdown'&&job.createdAt>=policy.effectiveAt){line.textContent='后台正在安排 5 秒倒计时。';}
 else line.textContent='此历史任务需点击“立即执行”，不会自动补交。';
}}
async function refresh(){if(!token||busy||refreshing)return;refreshing=true;try{
 if(!policy){policy=await api('/v1/execution-policy');
  $('execution-policy').textContent=policy.mode==='background-countdown'?'已开启后台自动执行：新任务准备完成后倒计时 5 秒，未取消就自动提交，可能立即消耗积分。关闭本页、切换标签页都不会暂停。':'当前为手动执行：任务不会自动提交，请在任务卡片点击“立即执行”。';}
 if(!Object.keys(catalog).length){for(const path of ['/v1/models','/enhancement/v1/models','/extension/v1/models','/image/v1/models','/audio/v1/models']){const m=await api(path);for(const x of m.data)catalog[x.id]=x.name;}}
 const {data,serverTime}=await api('/v1/jobs');if(Number.isFinite(serverTime))serverOffset=serverTime-Date.now()/1000;
 $('login').hidden=true;$('jobs').replaceChildren();countdowns.clear();$('message').textContent='';
 if(!data.length)text($('jobs'),'p',policy.mode==='background-countdown'?'尚无任务。在 BeefTV 提交后，后台会在准备完成 5 秒后自动执行，可在这里取消。':'尚无任务。在 BeefTV 提交后，可在这里手动执行。');
 for(const job of data){const card=document.createElement('section');card.className='job';$('jobs').append(card);
 text(card,'h2',(job.modelLabel||catalog[job.model]||'历史预检任务')+' · '+(labels[job.status]||job.status));
 const enhanced=job.kind==='enhancement', extended=job.kind==='extension', source=job.sourceVideo, image=job.kind==='image', audio=job.kind==='audio';
 const durationText=extended?`原视频 ${source?.duration??'?'} 秒，新增 ${job.duration||'?'} 秒`:enhanced?`原视频 ${source?.duration??'?'} 秒，计费 ${job.duration||'?'} 秒`:`${job.duration||'?'} 秒`;
 text(card,'p',image?`${job.aspect_ratio} · ${job.resolution} · 预计 ${job.pointsEstimate??'计算中'} 积分`:audio?`${job.pointsDescription||'音频生成'} · 预计 ${job.pointsEstimate??'计算中'} 积分`:enhanced||extended?`${durationText} · 原视频 ${source?.width??'?'} × ${source?.height??'?'} · 预计 ${job.pointsEstimate??'计算中'} 积分`:`${durationText} · 9:16 · 分镜 ${job.storyboardId||'准备中'} · 预计 ${job.pointsEstimate??'计算中'} 积分`,'meta');
 text(card,'small','任务 '+job.id);
 if(enhanced)text(card,'p','增强整段输入视频；保持原视频时长，保留原音轨。节点的提示词、生成秒数和画幅设置不用于增强。预计费用按实际时长向上取整，最终以剧梦结算为准。');
 else text(card,'pre',job.prompt||'此历史记录未保存完整请求，请从 BeefTV 新建任务。');
 if(extended)text(card,'p','只返回新续写片段，原视频保留在原节点。右侧秒数为新增时长；画幅由剧梦根据原视频适配。预估费用包含原视频参考成本，最终以剧梦结算为准。');
 for(const ref of job.references||[]){const link=document.createElement('a');link.textContent=ref.name;link.href=ref.url;link.target='_blank';link.rel='noreferrer';card.append(link,document.createElement('br'));}
 if(job.error)text(card,'p',job.error);
 if(job.status==='prepared'&&job.quoteId&&job.model){
 const line=text(card,'p','','countdown');
 const button=text(card,'button',`立即执行 · ${job.pointsEstimate} 积分`);button.onclick=async()=>{
 busy=true;button.disabled=true;line.textContent='正在提交任务。';try{await api('/v1/jobs/'+encodeURIComponent(job.id)+'/approve',{confirm:true,quoteId:job.quoteId,points:job.pointsEstimate});}catch(e){alert(e.message);}finally{busy=false;refresh();}};
 const cancel=text(card,'button','取消此任务');cancel.onclick=async()=>{busy=true;cancel.disabled=true;button.disabled=true;countdowns.delete(job.id);line.textContent='正在取消任务。';try{await api('/v1/jobs/'+encodeURIComponent(job.id)+'/cancel',{});}catch(e){alert(e.message);}finally{busy=false;refresh();}};
 countdowns.set(job.id,{job,line,execute:button});}
 if(job.status==='completed'){for(const [index,key] of (image?job.mediaKeys||[]:[job.mediaKey]).entries()){const link=document.createElement('a');link.href='/media/'+key;link.textContent=image?'打开 / 下载图片 '+(index+1):audio?'打开 / 下载音频':'打开 / 下载视频';link.target='_blank';card.append(link,document.createElement('br'));}}
 }
 updateCountdowns();
 }catch(e){$('message').textContent=e.message;}finally{refreshing=false;}}
$('signin').onclick=()=>{token=$('key').value.trim();sessionStorage.setItem('dreama-local-key',token);refresh();};
refresh();setInterval(refresh,1000);setInterval(updateCountdowns,100);
