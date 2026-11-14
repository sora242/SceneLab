// Image-first inspiration workspace, separate from chat and rated exploration.
(()=>{
const el=id=>document.getElementById(id),notice=el('inspireStatus'),picker=el('inspireSessions');
let session=null,busy=false,stopRequested=false,modeProgressActive=false,cancelId=null;
const say=text=>{notice.textContent=text;if(modeProgressActive)updateProgress(text);};
function node(tag,text,css){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(css)n.className=css;return n;}
function button(text,css,action){const n=node('button',text,css);n.type='button';n.onclick=action;return n;}
async function api(path,body){if(body!==undefined)await window.flushParameterSaves?.();const r=await fetchWithLLMRetryStatus(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},text=>{if(!stopRequested)say(text);});const data=await readApiResponse(r);if(!r.ok)throw new Error(data.error||'処理に失敗しました。');return data;}
function available(){if(busy)return false;if(send.disabled||window.explorationBusy){say('ほかの処理が終わってから実行してください。');return false;}return true;}
function setBusy(value,generating=false){busy=value;window.inspirationBusy=value;document.querySelectorAll('#inspireView button,#inspireView input,#inspireView textarea,#inspireView select').forEach(n=>n.disabled=value);[send,directSend,consult,editSend].forEach(n=>n.disabled=value);el('inspireResume').hidden=value;el('inspireStop').hidden=true;el('inspireStop').disabled=false;el('inspireStop').textContent='この1枚の完了後に中止';}
async function refreshList(){const data=await api('/api/inspire');picker.replaceChildren(new Option('新しいテーマ',''));for(const s of data.sessions)picker.add(new Option(s.theme.replace(/\s+/g,' ').slice(0,60),s.id));picker.value=session?.id||'';}
function adoptedElementGroup(element){
 const norm=value=>value.trim().toLowerCase();
 const matches=session.groups.filter(group=>group.values.some(value=>norm(value)===norm(element.value)));
 return matches.find(group=>norm(group.name)===norm(element.group))||(matches.length===1?matches[0]:null);
}
async function editGroups(mutator){if(!available()||!session)return;const groups=structuredClone(session.groups);mutator(groups);setBusy(true);try{session=(await api('/api/inspire/groups',{session_id:session.id,groups})).session;if(notice.textContent.startsWith('保存できませんでした：'))notice.textContent='';}catch(e){say('保存できませんでした：'+e.message);}finally{setBusy(false);render();}}
function renderGroups(){
 const panel=el('inspireGroups');panel.replaceChildren();if(!session)return;
 session.groups.forEach((group,index)=>{
  const card=node('div',undefined,'inspireGroup'),head=node('div',undefined,'inspireGroupHead');
  const name=node('input');name.value=group.name;name.maxLength=80;name.setAttribute('aria-label','グループ名');name.onchange=()=>editGroups(groups=>{groups[index].name=name.value;});
  head.append(name,button('グループを削除','secondary',()=>editGroups(groups=>{groups.splice(index,1);})));card.append(head);
  const chips=node('div',undefined,'inspireChips');group.values.forEach((value,i)=>{const chip=node('span',undefined,'inspireChip');const remove=button('×','',()=>editGroups(groups=>{groups[index].values.splice(i,1);}));remove.setAttribute('aria-label',value+'を削除');chip.append(node('span',value),remove);chips.append(chip);});card.append(chips);
  const add=node('div',undefined,'inspireAdd'),input=node('input');input.placeholder='候補を追加';input.maxLength=200;input.setAttribute('aria-label',group.name+'の候補を追加');const commit=()=>{const v=input.value.trim();if(v)editGroups(groups=>{groups[index].values.push(v);});};input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();commit();}};add.append(input,button('追加','secondary',commit));card.append(add);panel.append(card);
 });
}
window.inspireParameterModel=()=>session?.settings.model||null;
function render(){
 window.dispatchEvent(new Event('parameter-model-change'));
 el('inspireFromPrompt').hidden=!!session;el('inspireStart').textContent=session?'いろいろ生成する':'テーマからひらめき';
 el('inspireSetup').hidden=!!session;el('inspireMaterials').hidden=!session;el('inspireDelete').hidden=!session;
 const summary=el('inspireSummary');summary.replaceChildren();
 const batches=el('inspireBatches');batches.replaceChildren();
 const last=session?.batches.at(-1);const remaining=last&&last.ideas.filter(i=>i.image).length<last.target_count;
 el('inspireResume').hidden=busy||!remaining;el('inspireStart').hidden=!!last&&last.status==='pending';
 if(!session){el('inspireGroups').replaceChildren();return;}
 summary.append(node('div',session.theme,'exploreThemeText'));const cfg=session.settings;
 summary.append(node('div',`${modelLabel(cfg.model)} ｜ ${window.imageSizeLabel?.(cfg.model,cfg.width,cfg.height)??(cfg.width+' × '+cfg.height)}${cfg.character_name?' ｜ '+cfg.character_name:''}（このテーマ内で固定）`,'sub'));
 renderGroups();
 for(const batch of [...session.batches].reverse()){
  const section=node('section',undefined,'exploreRound');section.append(node('h3',`生成 ${batch.number}${batch===last&&batch.status==='stopped'?' ・途中中止':''}`));const grid=node('div',undefined,'exploreGrid');section.append(grid);
  for(const idea of batch.ideas){if(!idea.image)continue;
   const card=node('article',undefined,'exploreCard'),titleRow=node('div',undefined,'imageTitleRow');titleRow.append(node('h4',idea.title));card.append(titleRow,node('div',idea.direction,'inspireBadge'));
   const img=node('img');img.src=idea.image.url;img.alt=idea.title;img.loading='lazy';img.onclick=()=>{const modal=node('div',undefined,'modalBackdrop open'),picture=node('img',undefined,'inspireZoom');picture.src=idea.image.url;picture.alt=idea.title;modal.append(picture);modal.onclick=()=>modal.remove();document.body.append(modal);};const imageWrap=node('div',undefined,'inspireImageWrap');imageWrap.append(img);card.append(imageWrap);
   const elements=node('div',undefined,'inspireElements');idea.elements.forEach((element,index)=>{const registered=adoptedElementGroup(element);const adopted=Boolean(registered);const groupName=registered?.name||element.group;const adopt=button((adopted?'✓ ':'＋ ')+element.value,adopted?'adopted':'',async()=>{
    if(!available())return;setBusy(true);try{session=(await api('/api/inspire/adopt',{session_id:session.id,batch_id:batch.id,idea_id:idea.id,element_index:index,action:adopted?'remove':'add'})).session;say('「'+element.value+'」を「'+groupName+'」の材料'+(adopted?'から削除しました。':'に追加しました。'));}catch(e){say(e.message);}finally{setBusy(false);render();}
   });adopt.title=adopted?'クリックで「'+groupName+'」の材料から削除':'クリックで「'+groupName+'」の材料に追加';adopt.setAttribute('aria-pressed',String(adopted));adopt.disabled=busy;elements.append(adopt);});card.append(node('div','今後も使用する材料に追加・削除','sub'),elements);
   const copy=button('ギャラリーにコピー','mutedSmall',async()=>{if(!available())return;copy.disabled=true;try{const data=await api('/api/inspire/copy-gallery',{session_id:session.id,batch_id:batch.id,idea_id:idea.id});const gid=data.image.info.gallery_id;idea.gallery_id=gid;updateGalleryCopyButton(copy,gid);if(!galleryItems.has(gid))addImages([data.image]);say(data.already_copied?'すでにギャラリーにあります。':'ギャラリーにコピーしました。');}catch(e){say(e.message);}finally{copy.disabled=busy;updateGalleryCopyButton(copy,idea.gallery_id);}});updateGalleryCopyButton(copy,idea.gallery_id);titleRow.append(createImageOptionsMenu(copy,idea.prompt));grid.append(card);
  }batches.append(section);
 }
 if(busy)document.querySelectorAll('#inspireView button,#inspireView input,#inspireView textarea,#inspireView select').forEach(n=>{n.disabled=n.id!=='inspireStop'||stopRequested;});
}
function step(delta){const field=el('inspireCount'),n=Number(field.value);field.value=String(Math.max(1,(Number.isFinite(n)?Math.floor(n):12)+delta));}
el('inspireMinus').onclick=()=>step(-1);el('inspirePlus').onclick=()=>step(1);
el('inspireAddGroup').onclick=()=>{const name=el('inspireGroupName').value.trim();if(!name)return;editGroups(groups=>{groups.push({name,values:[]});}).then(()=>{el('inspireGroupName').value='';});};
el('inspireGroupName').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();el('inspireAddGroup').click();}};
el('inspireExpand').onclick=async()=>{
 if(!available()||!session)return;
 stopRequested=false;setBusy(true);modeProgressActive=true;notice.hidden=true;
 const endProgress=startModeProgress('ひらめき ｜ 材料追加',()=>el('inspireStop').onclick());
 progressCancel.textContent='中止';
 say('AIが新しい材料を考えています…');
 try{
  session=(await cancellableApi('/api/inspire/expand',{session_id:session.id})).session;
  say('新しい材料を追加しました。不要なものは削除できます。');
 }catch(e){say(e.cancelled?'LLMの待機を中止しました。':e.message);}
 finally{modeProgressActive=false;notice.hidden=false;endProgress();setBusy(false);render();}
};
picker.onchange=async()=>{if(!available()){picker.value=session?.id||'';return;}const id=picker.value;setBusy(true);try{session=id?(await api('/api/inspire?id='+encodeURIComponent(id))).session:null;el('inspireCount').value=String(session?.settings.count||12);say(session?'保存したテーマを開きました。材料を編集して、また生成できます。':'テーマを入力してください。');}catch(e){picker.value=session?.id||'';say(e.message);}finally{setBusy(false);render();}};
el('inspireNew').onclick=()=>{if(!available())return;session=null;picker.value='';el('inspireTheme').value='';el('inspireCount').value='12';render();say('ざっくりしたテーマを入力してください。');};
el('inspireDelete').onclick=async()=>{if(!available()||!session)return;if(!await appConfirm('このテーマと全ての生成画像・材料・プロンプトを削除しますか？元に戻せません。'))return;setBusy(true);try{await api('/api/inspire/delete',{session_id:session.id});session=null;el('inspireTheme').value='';await refreshList();say('テーマを削除しました。');}catch(e){say(e.message);}finally{setBusy(false);render();}};
el('inspireStop').onclick=async()=>{
 stopRequested=true;
 el('inspireStop').disabled=true;
 el('inspireStop').textContent='中止予約済み';
 const id=cancelId;
 if(!id){say('処理が完了したところで中止します。');return;}
 try{
  const result=await cancelLlmWait(id);
  if(cancelId===id&&!result.cancelled)say('処理中の1枚が完成したら中止します。');
 }catch(e){if(cancelId===id)say(e.message+' 現在の処理が終わったら中止します。');}
};
async function cancellableApi(path,body){
 const id=crypto.randomUUID();
 cancelId=id;
 try{
  const result=await api(path,{...body,cancel_id:id});
  if(result.cancelled){
   const error=Error('LLMの待機を中止しました。');
   error.cancelled=true;
   throw error;
  }
  return result;
 }finally{cancelId=null;}
}
async function run(resume=false,fromPrompt=false){
 if(!available())return;if(!resume&&!el('inspireCount').reportValidity())return;
 if(!session&&!el('inspireTheme').value.trim()){say('テーマまたは最終プロンプトを入力してください。');return;}
 let completionMessage="",llmCancelled=false,attemptedImage=false;
 stopRequested=false;setBusy(true,true);
 modeProgressActive=true;notice.hidden=true;const endProgress=startModeProgress('ひらめき',()=>el('inspireStop').onclick());
 progressCancel.textContent='中止';
 try{
  if(!session){say('AIが発想の材料を作っています…');session=(await cancellableApi('/api/inspire/create',{theme:el('inspireTheme').value.trim(),from_final_prompt:fromPrompt,count:Number(el('inspireCount').value),model:generationModel.value,width:snapDim(widthInput),height:snapDim(heightInput),character_id:window.characterForGeneration?window.characterForGeneration():selectedCharacterId})).session;render();await refreshList();}
  if(stopRequested){say('中止しました。');return;}
  if(!resume){session=(await api('/api/inspire/start',{session_id:session.id,count:Number(el('inspireCount').value)})).session;render();}
  let batch=session.batches.at(-1);if(!batch)throw new Error('再開する生成がありません。');
  while(batch.ideas.length<batch.target_count&&!stopRequested){
   const prepared=batch.ideas.length;
   say(`画像用プロンプトを作成しています… ${prepared} / ${batch.target_count}件準備済み（最大8件ずつ）`);
   session=(await cancellableApi('/api/inspire/prepare',{session_id:session.id,batch_id:batch.id})).session;
   batch=session.batches.at(-1);
   if(batch.ideas.length<=prepared)throw Error('画像用プロンプトが増えませんでした。');
   render();
  }
  if(!stopRequested)progressCancel.textContent='この1枚の完了後に中止';
  while(batch.ideas.filter(i=>i.image).length<batch.target_count&&!stopRequested){
   const done=batch.ideas.filter(i=>i.image).length;
   say(`画像を生成しています… ${done+1} / ${batch.target_count}枚`);
   attemptedImage=true;
   session=(await api('/api/inspire/next',{session_id:session.id,batch_id:batch.id,request_id:crypto.randomUUID(),prepared_only:true})).session;
   batch=session.batches.at(-1);render();
  }
  if(stopRequested&&batch.ideas.filter(i=>i.image).length<batch.target_count){session=(await api('/api/inspire/stop',{session_id:session.id})).session;completionMessage='ひらめき：画像生成を中止しました';say('中止しました。残りを再開するか、材料を変えて新しく生成できます。');}
  else {completionMessage='ひらめき：画像生成が完了しました（'+batch.target_count+'枚）';say('生成が完了しました。画像下の要素ボタンで、材料に追加・削除できます。');}
 }catch(e){
  if(e.cancelled){
   llmCancelled=true;
   if(session?.batches.at(-1)?.status==='pending'){
    try{session=(await api('/api/inspire/stop',{session_id:session.id})).session;}catch(_e){}
   }
   say('LLMの待機を中止しました。');
  }else{
   say('処理を停止しました：'+e.message);
   if(session){try{session=(await api('/api/inspire?id='+encodeURIComponent(session.id))).session;}catch(_e){}}
  }
 }
 finally{if(attemptedImage&&!llmCancelled){try{await api('/api/comfy/free',{});}catch(_e){}}modeProgressActive=false;notice.hidden=false;endProgress();setBusy(false);render();if(completionMessage)notifyImageCompletion(completionMessage);}
}
window.prepareInspirationFromPrompt=prompt=>{
 if(!available()){status.textContent='処理が完了してからひらめきを開始してください。';return;}
 session=null;picker.value='';el('inspireTheme').value=prompt;render();showView('inspire');el('inspireTheme').focus();
 say('最終プロンプトを引き継ぎました。「最終プロンプトからひらめき」で材料に分解し、発想を広げます。');
};
el('inspireStart').onclick=()=>run(false);el('inspireFromPrompt').onclick=()=>run(false,true);el('inspireResume').onclick=()=>run(true);
window.addEventListener('beforeunload',e=>{if(busy){e.preventDefault();e.returnValue='';}});
refreshList().then(render).catch(e=>say(e.message));
})();
