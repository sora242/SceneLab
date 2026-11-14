// Independent history and persistent feedback for exploration mode.
(()=>{
const byId=id=>document.getElementById(id);
const picker=byId('exploreSessions'),theme=byId('exploreTheme'),start=byId('exploreStart'),resume=byId('exploreResume'),stop=byId('exploreStop'),notice=byId('exploreStatus');
let session=null,busy=false,stopRequested=false,saving=null,saveTimer=null,modeProgressActive=false,cancelId=null;
const pending=new Map();
function stepExploreCount(delta){const el=byId('exploreCount');const n=Number(el.value);el.value=String(Math.max(1,(Number.isFinite(n)?Math.floor(n):4)+delta));}
byId('exploreCountMinus').onclick=()=>stepExploreCount(-1);
byId('exploreCountPlus').onclick=()=>stepExploreCount(1);
const say=message=>{notice.textContent=message;if(modeProgressActive)updateProgress(message);};
async function api(path,body){if(body!==undefined)await window.flushParameterSaves?.();const r=await fetchWithLLMRetryStatus(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},text=>{if(!stopRequested)say(text);});const d=await readApiResponse(r);if(!r.ok)throw new Error(d.error||'保存・生成に失敗しました。');return d;}
function setBusy(value){busy=value;window.explorationBusy=value;[start,byId('exploreFromPrompt'),resume,byId('exploreNew'),byId('exploreDelete'),byId('exploreResetScores'),picker,theme,byId('exploreCount'),byId('exploreCountMinus'),byId('exploreCountPlus'),send,directSend,consult,editSend].forEach(el=>el.disabled=value);document.querySelectorAll('#exploreRounds button,#exploreRounds textarea').forEach(el=>el.disabled=value);if(value)resume.hidden=true;stop.hidden=true;stop.disabled=false;stop.textContent='この1枚の完了後に中止';}
function node(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function queueRating(rnd,idea){const key=rnd.id+':'+idea.id;pending.set(key,{session_id:session.id,round_id:rnd.id,idea_id:idea.id,score:idea.score,comment:idea.comment});clearTimeout(saveTimer);saveTimer=setTimeout(()=>flush().catch(()=>{}),450);}
async function flush(){clearTimeout(saveTimer);if(saving)await saving;if(!pending.size)return;saving=(async()=>{while(pending.size){const [key,value]=pending.entries().next().value;try{await api('/api/explore/rate',value);}catch(e){say('評価を保存できませんでした。次の変更時またはラウンド開始時に再試行します。 '+e.message);throw e;}if(pending.get(key)===value)pending.delete(key);}if(notice.textContent.startsWith('評価を保存できませんでした。'))notice.textContent='';})();try{await saving;}finally{saving=null;}}
window.exploreParameterModel=()=>session?.settings.model||null;
function render(){
 window.dispatchEvent(new Event('parameter-model-change'));
 byId('exploreSetup').hidden=!!session;
 byId('exploreDelete').hidden=!session;
 byId('exploreResetScores').hidden=!session;
 const summary=byId('exploreThemeSummary');summary.replaceChildren();
 const rounds=byId('exploreRounds');rounds.replaceChildren();
 start.textContent=session?'評価を反映して次のラウンドへ':'テーマから探索';
 byId('exploreFromPrompt').hidden=!!session;
 const last=session?.rounds.at(-1);resume.hidden=busy||!last||!last.ideas.some(idea=>!idea.image);
 start.hidden=!!last&&!['completed','stopped'].includes(last.status);
 if(!session)return;
 summary.append(node('div',session.theme,'exploreThemeText'));
 const cfg=session.settings;summary.append(node('div',`${modelLabel(cfg.model)} ｜ ${window.imageSizeLabel?.(cfg.model,cfg.width,cfg.height)??(cfg.width+' × '+cfg.height)}${cfg.character_name?' ｜ '+cfg.character_name:''}（このテーマ内で固定）`,'sub'));
 for(const rnd of [...session.rounds].reverse()){
  const section=node('section',undefined,'exploreRound');section.append(node('h3',`ラウンド ${rnd.number}${rnd.id===last.id&&rnd.status==='stopped'?' ・途中中止':''}`));const grid=node('div',undefined,'exploreGrid');section.append(grid);
  for(const idea of rnd.ideas){
   if(rnd.id!==last.id&&!idea.image)continue;
   const card=node('article',undefined,'exploreCard'),titleRow=node('div',undefined,'imageTitleRow');titleRow.append(node('h4',idea.title));card.append(titleRow);
   if(idea.image){const img=node('img');img.src=idea.image.url;img.alt=idea.title;img.loading='lazy';img.onclick=()=>{const modal=node('div',undefined,'modalBackdrop open');const picture=node('img');picture.src=idea.image.url;picture.alt=idea.title;picture.style.cssText='max-width:94vw;max-height:94vh;object-fit:contain;cursor:zoom-out';modal.append(picture);modal.onclick=()=>modal.remove();document.body.append(modal);};card.append(img);
    const copy=node('button','ギャラリーにコピー','mutedSmall');copy.type='button';updateGalleryCopyButton(copy,idea.gallery_id);
    copy.onclick=async()=>{
     copy.disabled=true;copy.textContent='コピー中…';
     try{
      const data=await api('/api/explore/copy-gallery',{session_id:session.id,round_id:rnd.id,idea_id:idea.id});
      const gid=data.image.info.gallery_id;idea.gallery_id=gid;updateGalleryCopyButton(copy,gid);if(!galleryItems.has(gid))addImages([data.image]);
      say(data.already_copied?'この画像はすでにギャラリーにあります。':'メインギャラリーにコピーしました。');
     }catch(e){say('コピーできませんでした：'+e.message);}
     finally{copy.disabled=busy;updateGalleryCopyButton(copy,idea.gallery_id);}
    };titleRow.append(createImageOptionsMenu(copy,idea.prompt));
    const rating=node('div',undefined,'exploreRating');const buttons=[];
    function update(){buttons.forEach((btn,index)=>{const selected=idea.score===index;btn.classList.toggle('chosen',selected);btn.setAttribute('aria-pressed',String(selected));});}
    for(let score=0;score<=4;score++){const btn=node('button',score===0?'0 ♡':score+' ♥');btn.type='button';btn.setAttribute('aria-label','ハート'+score);btn.onclick=()=>{idea.score=score;update();queueRating(rnd,idea);};rating.append(btn);buttons.push(btn);}
    card.append(rating);update();
    const comment=node('textarea');comment.rows=2;comment.placeholder='好み・改善点（任意）';comment.maxLength=2000;comment.value=idea.comment;comment.setAttribute('aria-label',idea.title+'へのコメント');comment.oninput=()=>{idea.comment=comment.value;queueRating(rnd,idea);};card.append(comment);
   }else card.append(node('div',rnd.status==='stopped'?'未生成（中止）':'生成待ち','explorePending'));
   if(!idea.image){const details=node('details');details.append(node('summary','最終プロンプト'),node('div',idea.prompt,'explorePrompt'));card.append(details);}grid.append(card);
  }rounds.append(section);
 }
 document.querySelectorAll('#exploreRounds button,#exploreRounds textarea').forEach(el=>el.disabled=busy);
}
async function refreshList(){const data=await api('/api/explore');picker.replaceChildren(new Option('新しいテーマ',''));for(const item of data.sessions)picker.add(new Option(item.theme.replace(/\s+/g,' ').slice(0,60),item.id));picker.value=session?.id||'';}
async function selectSession(id){await flush();session=id?(await api('/api/explore?id='+encodeURIComponent(id))).session:null;byId('exploreCount').value=String(session?.settings.count||4);render();say(session?'画像を評価し、次のラウンドへ進めます。':'テーマまたは最終プロンプトを入力し、開始方法を選んでください。');}
picker.onchange=()=>{const id=picker.value;picker.disabled=true;selectSession(id).catch(e=>{picker.value=session?.id||'';say(e.message);}).finally(()=>{picker.disabled=false;});};
byId('exploreNew').onclick=async()=>{try{await selectSession('');picker.value='';theme.value='';theme.focus();}catch(e){say(e.message);}};
byId('exploreResetScores').onclick=async()=>{
 if(busy||!session)return;
 if(send.disabled){say('ほかの処理が完了してから評価をリセットしてください。');return;}
 if(!await appConfirm('このテーマの全ラウンドのハート評価を0に戻しますか？コメントは残ります。',{confirmLabel:'リセットする'}))return;
 setBusy(true);stop.hidden=true;
 try{
  await flush();
  const data=await api('/api/explore/reset-scores',{session_id:session.id});
  session=data.session;render();say('このテーマの全てのハート評価を0に戻しました。コメントは残しています。');
 }catch(e){say('評価をリセットできませんでした：'+e.message);}
 finally{setBusy(false);render();}
};
byId('exploreDelete').onclick=async()=>{
 if(busy||!session)return;
 if(send.disabled){say('ほかの処理が完了してから削除してください。');return;}
 if(!await appConfirm('このテーマを削除しますか？\n\n'+session.theme.slice(0,180)+'\n\nこのテーマの全ラウンド・画像・プロンプト・評価を削除します。元に戻せません。'))return;
 const sid=session.id;clearTimeout(saveTimer);setBusy(true);stop.hidden=true;
 try{
  if(saving)await saving.catch(()=>{});
  await api('/api/explore/delete',{session_id:sid});
  pending.clear();session=null;theme.value='';render();await refreshList();say('テーマと関連する画像・評価を削除しました。');
 }catch(e){say('削除できませんでした：'+e.message);}
 finally{setBusy(false);render();}
};
stop.onclick=async()=>{
 stopRequested=true;stop.disabled=true;stop.textContent='中止予約済み';
 const id=cancelId;
 if(!id){say('処理中の1枚が完成したら中止します。');return;}
 say('LLMの待機を中止しています…');
 try{
  const result=await cancelLlmWait(id);
  if(cancelId===id&&!result.cancelled)say('処理が完了したところで中止します。');
 }catch(e){if(cancelId===id)say(e.message+' 現在の処理が終わったら中止します。');}
};
async function cancellableApi(path,body){
 const id=crypto.randomUUID();cancelId=id;
 try{
  const result=await api(path,{...body,cancel_id:id});
  if(result.cancelled){const error=Error('LLMの待機を中止しました。');error.cancelled=true;throw error;}
  return result;
 }finally{cancelId=null;}
}
async function run(makePlan,fromPrompt=false){
 if(busy)return;
 if(makePlan&&!byId('exploreCount').reportValidity())return;
 if(send.disabled){say('ほかの生成・会話が完了してから開始してください。');return;}
 try{await flush();}catch(e){return;}
 if(makePlan&&!session&&!theme.value.trim()){say('テーマまたは最終プロンプトを入力してください。');theme.focus();return;}
 if(makePlan&&session&&!session.rounds.some(r=>r.ideas.some(i=>i.image))){say('画像を1枚以上生成してください。');return;}
 let completionMessage="",llmCancelled=false;
 stopRequested=false;setBusy(true);
 modeProgressActive=true;notice.hidden=true;const endProgress=startModeProgress('探索',()=>stop.onclick());
 try{
  if(makePlan){progressCancel.textContent='中止';say('AIが複数のプロンプトを作成しています…');
   const cfg=session?{session_id:session.id,count:Number(byId('exploreCount').value)}:{theme:theme.value.trim(),from_final_prompt:fromPrompt,count:Number(byId('exploreCount').value),model:generationModel.value,width:snapDim(widthInput),height:snapDim(heightInput),character_id:window.characterForGeneration?window.characterForGeneration():selectedCharacterId};
   const data=await cancellableApi('/api/explore/plan',cfg);session=data.session;render();await refreshList();
  }
  if(!stopRequested)progressCancel.textContent='この1枚の完了後に中止';
  const rnd=session.rounds.at(-1);
  for(const idea of rnd.ideas){
   if(stopRequested)break;if(idea.image)continue;
   say(`ラウンド ${rnd.number}：画像生成中 ${rnd.ideas.indexOf(idea)+1} / ${rnd.ideas.length}`);
   const data=await api('/api/explore/generate',{session_id:session.id,round_id:rnd.id,idea_id:idea.id});session=data.session;render();
  }
  if(stopRequested&&session.rounds.at(-1).ideas.some(idea=>!idea.image)){session=(await api('/api/explore/stop',{session_id:session.id})).session;render();completionMessage='探索：画像生成を中止しました';say('中止しました。未生成の候補を再開するか、評価して次のラウンドへ進めます。');}
  else {completionMessage='探索：ラウンドの画像生成が完了しました';say('ラウンドが完了しました。ハート0〜4で評価してください。評価していない画像は0として扱います。');}
 }catch(e){if(e.cancelled){llmCancelled=true;say('LLMの待機を中止しました。');}else{say('処理を停止しました：'+e.message+' 保存済みの候補は「未生成の候補を生成」から再開できます。');}}
 finally{
  // Release once per exploration round, including a partially failed or stopped round.
  if(!llmCancelled){try{await api('/api/comfy/free',{});}catch(e){}}
  modeProgressActive=false;notice.hidden=false;endProgress();
  setBusy(false);render();
  if(completionMessage)notifyImageCompletion(completionMessage);
 }
}
window.prepareExplorationFromPrompt=async prompt=>{
 if(busy||send.disabled||window.inspirationBusy){say('処理が完了してから探索を開始してください。');status.textContent='処理が完了してから探索を開始してください。';return;}
 try{
  await flush();session=null;picker.value='';theme.value=prompt;render();showView('explore');theme.focus();
  say('最終プロンプトを引き継ぎました。「最終プロンプトから探索」で派生画像を生成できます。');
 }catch(e){status.textContent='探索に引き継げませんでした：'+e.message;}
};
start.onclick=()=>run(true);byId('exploreFromPrompt').onclick=()=>run(true,true);resume.onclick=()=>run(false);
window.addEventListener('beforeunload',e=>{if(pending.size||busy){e.preventDefault();e.returnValue='';}});
refreshList().then(()=>{render();}).catch(e=>say(e.message));
})();
