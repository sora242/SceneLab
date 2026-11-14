(() => {
 const el=id=>document.getElementById(id),page=el('settingsView');
 const labels={checkpoint:'一体型SDXLチェックポイント',diffusion:'画像モデル',text_encoder:'テキストエンコーダー',vae:'VAE',steps:'Steps',cfg:'CFG',sampler_name:'Sampler',scheduler:'Scheduler',sigmas:'Sigmas（カンマ区切り）'};
 let data={models:[],connected:false},view=window.currentAppView||'chat',loading=null;
 const drafts=new Map();
 const state=document.createElement('p');state.setAttribute('role','status');
 const refresh=button('ComfyUIに接続・一覧を更新',async()=>{
  if(await saveConnection()){
   await loading;
   await load();
  }
 });
 const connection=document.createElement('section');connection.className='modelSettingsCard';connection.style.marginTop='20px';
 const connectionTitle=document.createElement('h3');connectionTitle.textContent='ComfyUIの接続先';
 const connectionHint=document.createElement('p');connectionHint.className='sub';
 connectionHint.textContent='ComfyUIを開いているブラウザのアドレスバーからURLをコピーして貼り付けてください。URLはComfyUIの起動ログでも確認できます。入力したURLは自動で保存されます。';
 const connectionUrl=document.createElement('input');connectionUrl.type='url';connectionUrl.placeholder='http://127.0.0.1:8188';
 const connectionReport=document.createElement('p');connectionReport.setAttribute('role','status');
 connection.append(connectionTitle,connectionHint);
 field(connection,'ComfyUIのURL',connectionUrl);
 const comfyUnload=document.createElement('input');comfyUnload.type='checkbox';
 const comfyUnloadLabel=document.createElement('label');
 comfyUnloadLabel.append(comfyUnload,document.createTextNode(' 画像生成後にComfyUIのモデルをアンロード'));
 connection.append(comfyUnloadLabel,connectionReport,state,refresh);
 let savedConnectionUrl,savedComfyUnload=false,connectionTimer,connectionSave=Promise.resolve(true);
 connectionUrl.disabled=comfyUnload.disabled=true;
 comfyUnload.onchange=saveConnection;
 connectionUrl.oninput=()=>{
  clearTimeout(connectionTimer);
  connectionReport.textContent='';
  connectionTimer=setTimeout(saveConnection,500);
 };
 connectionUrl.onchange=saveConnection;
 const content=document.createElement('div');content.className='modelSettingsGrid';
 const title=document.createElement('h2');title.textContent='設定';
 const intro=document.createElement('p');intro.textContent='LLMとComfyUIの接続先、画像モデルのファイルを設定します。生成・編集パラメータは各モードの左側で変更できます。';
 const wrap=document.createElement('div');wrap.className='modelSettingsPage';wrap.append(title,intro,llmConnectionCard(),connection,content);page.append(wrap);
 const panel=document.createElement('div');panel.id='modelParameters';panel.className='controlGroup';el('leftPanel').append(panel);
 const sizeGroup=el('sizeGroup');
 const sizeHint=document.createElement('div');
 sizeHint.className='sub';
 sizeHint.setAttribute('role','status');
 sizeGroup.append(sizeHint);
 window.imageSizeLabel=(id,width,height)=>{
  const model=data.models.find(item=>item.id===id);
  if(model?.backend!=='user_workflow')return width+' × '+height;
  const {bindings={}}=model;
  if(bindings.width&&bindings.height)return width+' × '+height;
  if(bindings.width)return '幅 '+width+' ／ 高さはワークフロー指定';
  if(bindings.height)return '幅はワークフロー指定 ／ 高さ '+height;
  return '画像サイズはワークフロー指定';
 };
 function syncSizeControls(model){
  const themeModel=view==='explore'?window.exploreParameterModel?.():view==='inspire'?window.inspireParameterModel?.():null;
  el('generationModel').disabled=Boolean(themeModel);
  el('characterSelect').disabled=Boolean(themeModel);
  const usesWorkflow=model?.backend==='user_workflow';
  const widthLocked=usesWorkflow&&!model.bindings?.width;
  const heightLocked=usesWorkflow&&!model.bindings?.height;
  el('width').disabled=Boolean(themeModel)||widthLocked;
  el('height').disabled=Boolean(themeModel)||heightLocked;
  for(const preset of sizeGroup.querySelectorAll('.preset'))preset.disabled=el('width').disabled||el('height').disabled;
  sizeHint.textContent=widthLocked&&heightLocked
   ? '画像サイズはワークフローで決まります。ここでは変更できません。'
   : widthLocked ? '幅はワークフローで決まります。'
   : heightLocked ? '高さはワークフローで決まります。'
   : themeModel ? 'このテーマの画像サイズは作成時の指定で固定されています。' : '';
  sizeHint.hidden=!sizeHint.textContent;
 }
 const originalLabel=modelLabel;modelLabel=value=>data.models.find(m=>m.id===value)?.label||originalLabel(value);
 function removeButton(text,action){const b=button(text,action);b.classList.add('danger');return b;}
 function button(text,action){const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=text;b.onclick=action;return b;}
 async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const d=await readApiResponse(r);if(!r.ok)throw Error(d.error||'設定を取得できません。');return d;}
 function llmConnectionCard(){
  const card=document.createElement('section');card.className='modelSettingsCard';
  const title=document.createElement('h3');title.textContent='LLMの接続先';
  const hint=document.createElement('p');hint.className='sub';
  hint.textContent='OpenAI互換APIを使います。使用するサーバーのAPI URLとモデルIDを指定してください。クラウドを選ぶと、会話履歴・テーマ・材料・キャラクターのメモなど、LLMへの入力が接続先に送信されます。';
  const url=document.createElement('input');url.type='url';
  const model=document.createElement('input');model.type='text';model.maxLength=300;
  const apiKey=document.createElement('input');apiKey.type='password';apiKey.autocomplete='new-password';apiKey.maxLength=2048;
  const keyHint=document.createElement('p');keyHint.className='sub';
  keyHint.textContent='APIキーは接続先ごとに保存されます。初めて使う接続先では、発行したキーを貼り付けてください。';
  const picker=document.createElement('div');picker.hidden=true;
  const models=document.createElement('select');models.className='select';
  field(picker,'取得したモデルから選択',models);
  models.onchange=()=>{
   if(!models.value)return;
   model.value=models.value;
   notice.textContent='';
   save();
  };
  const notice=document.createElement('p');notice.className='sub';notice.setAttribute('role','status');
  let saved,timer,queue=Promise.resolve(true),urlRevision=0;
  const removeKey=button('この接続先のAPIキーを解除',()=>{
   apiKey.value='';
   notice.textContent='';
   save(true);
  });
  function showKeyState(){
   let active;
   try{if(new URL(url.value).origin===new URL(saved?.url).origin)active=saved;}catch(_e){}
   apiKey.placeholder=active?.has_api_key?'設定済み（貼り付けて更新）':active?.key_env?'環境変数を使用中':'';
   removeKey.hidden=!(active?.has_api_key||active?.key_env);
  }
  showKeyState();
  const list=button('LLMのモデル一覧を取得',async()=>{
   if(!url.value.trim()){
    notice.textContent='先にLLMのAPI URLを入力してください。';
    return;
   }
   const revision=urlRevision;
   list.disabled=true;
   picker.hidden=true;
   notice.textContent='モデル一覧を取得しています…';
   try{
    if(!await save()||revision!==urlRevision)return;
    const result=await api('/api/llm/models',{url:url.value});
    if(revision!==urlRevision)return;
    const placeholder=document.createElement('option');
    placeholder.value='';placeholder.textContent='モデルを選択…';placeholder.disabled=true;
    models.replaceChildren(placeholder);
    for(const id of result.models){
     const option=document.createElement('option');option.value=id;option.textContent=id;models.append(option);
    }
    models.value=result.models.includes(model.value)?model.value:'';
    picker.hidden=result.models.length===0;
    notice.textContent=picker.hidden?'モデル一覧が空です。モデルIDは手入力できます。':'';
   }catch(e){
    if(revision===urlRevision)notice.textContent=e.message;
   }finally{
    list.disabled=false;
   }
  });
  card.append(title,hint);
  field(card,'LLMのAPI URL',url);
  field(card,'APIキー（任意）',apiKey);
  card.append(keyHint,removeKey,list,picker);
  field(card,'モデルID（手入力も可）',model);
  const unloadSection=document.createElement('div');unloadSection.hidden=true;
  const unloadProvider=document.createElement('select');unloadProvider.className='select';unloadProvider.disabled=true;
  for(const [value,label] of [['','しない'],['ollama','Ollama'],['lmstudio','LM Studio'],['strata','Strata（完全アンロード）'],['strata_elastic','Strata（VRAM Elastic）']]){
   const option=document.createElement('option');option.value=value;option.textContent=label;unloadProvider.append(option);
  }
  field(unloadSection,'画像生成前のLLMのVRAM解放',unloadProvider);
  const reserveSection=document.createElement('div');
  const reserve=document.createElement('input');reserve.type='number';reserve.min='0';reserve.max='1048576';reserve.step='1000';reserve.value='16000';reserve.disabled=true;reserve.className='input';
  field(reserveSection,'画像生成前に確保する空きVRAM（MiB）',reserve);
  const elasticHint=document.createElement('p');elasticHint.className='sub';elasticHint.textContent='Strataの起動設定（JSON）で "vram_elastic": true にしてStrataを再起動してください。指定値はGPU全体の目標空き容量で、追加の解放量ではありません。モデルの一部はVRAMに残ります。縮小後は次のLLM要求前に1回だけ復元します。目標未達や調整失敗でも生成を続けます。ComfyUIがVRAMを保持している場合は、画像生成後のモデル解放も検討してください。詳しくはREADMEを参照してください。';
  reserveSection.append(elasticHint);unloadSection.append(reserveSection);
  reserve.onchange=()=>save();
  const unloadHint=document.createElement('p');unloadHint.className='sub';
  unloadHint.textContent='LM Studioでは Developer → Server Settings の Just in Time Model Loading をオンにしてください。';
  unloadSection.append(unloadHint);
  card.append(unloadSection,notice);
  function syncUnloadOption(){
   let local=false;
   try{local=['127.0.0.1','localhost','[::1]'].includes(new URL(url.value).hostname);}catch(_e){}
   unloadSection.hidden=!local;
   if(!local)unloadProvider.value='';
   unloadHint.hidden=unloadProvider.value!=='lmstudio';
   reserveSection.hidden=unloadProvider.value!=='strata_elastic';
  }
  unloadProvider.onchange=()=>{syncUnloadOption();save();};
  url.disabled=model.disabled=apiKey.disabled=list.disabled=true;
  api('/api/llm/connection').then(settings=>{
   saved=settings;
   url.value=settings.url;
   model.value=settings.model;
   unloadProvider.value=settings.unload_provider||'';
   reserve.value=settings.strata_reserve_mib??16000;
   syncUnloadOption();
   showKeyState();
  }).catch(e=>{
   notice.textContent=e.message;
  }).finally(()=>{
   url.disabled=model.disabled=apiKey.disabled=list.disabled=unloadProvider.disabled=reserve.disabled=false;
  });
  function save(remove=false){
   clearTimeout(timer);
   queue=queue.then(async()=>{
    const input={url:url.value,model:model.value,unload_provider:unloadProvider.value,strata_reserve_mib:Number(reserve.value)};
    if(!reserve.value.trim()||!Number.isInteger(input.strata_reserve_mib)||input.strata_reserve_mib<0||input.strata_reserve_mib>1048576){notice.textContent='空きVRAMを0〜1048576 MiBの整数で指定してください。';return false;}
    const secret=apiKey.value;
    if(secret||remove)input.api_key=remove?'':secret;
    if(!('api_key' in input)&&input.url===saved?.url&&input.model===saved.model&&input.unload_provider===(saved.unload_provider||'')&&input.strata_reserve_mib===(saved.strata_reserve_mib??16000))return true;
    try{
     saved=await api('/api/llm/connection',input);
     if(input.url===url.value)url.value=saved.url;
     if(input.model===model.value)model.value=saved.model;
     if(input.unload_provider===unloadProvider.value)unloadProvider.value=saved.unload_provider||'';
     syncUnloadOption();
     if(!remove&&secret===apiKey.value)apiKey.value='';
     showKeyState();
     return true;
    }catch(e){
     notice.textContent='保存できません：'+e.message;
     return false;
    }
   });
   return queue;
  }
  for(const input of [url,model,apiKey]){
   input.oninput=event=>{
    clearTimeout(timer);
    notice.textContent='';
    if(input===url){apiKey.value='';syncUnloadOption();showKeyState();}
    if(input!==model){urlRevision++;picker.hidden=true;models.replaceChildren();}
    else if(!picker.hidden)models.value=[...models.options].some(option=>option.value===model.value)?model.value:'';
    if(input!==apiKey||event?.inputType==='insertFromPaste')timer=setTimeout(save,500);
   };
   input.onchange=()=>save();
  }
  return card;
 }
 function saveConnection(){
  clearTimeout(connectionTimer);
  if(connectionUrl.disabled)return connectionSave;
  const input=connectionUrl.value,unload=Boolean(comfyUnload.checked);
  const current=()=>input===connectionUrl.value&&unload===Boolean(comfyUnload.checked);
  const alreadySaved=()=>connectionUrl.value===savedConnectionUrl&&Boolean(comfyUnload.checked)===savedComfyUnload;
  connectionSave=connectionSave.then(async()=>{
   if(!current())return alreadySaved();
   const url=input.trim();
   if(url===savedConnectionUrl&&unload===savedComfyUnload)return true;
   try{
    await pending;
    if(!current())return alreadySaved();
    connectionReport.textContent='';
    const result=await api('/api/comfy/connection',{url,unload_after_generation:unload});
    const changedUrl=savedConnectionUrl!==result.url;
    savedConnectionUrl=result.url;
    savedComfyUnload=Boolean(result.unload_after_generation);
    if(input===connectionUrl.value)connectionUrl.value=result.url;
    if(unload===Boolean(comfyUnload.checked))comfyUnload.checked=savedComfyUnload;
    if(changedUrl&&data.url!==result.url)state.textContent='現在の接続先は未確認です。';
    return true;
   }catch(e){
    if(input===connectionUrl.value)connectionReport.textContent='保存できません：'+e.message;
    return false;
   }
  });
  return connectionSave;
 }
 function field(parent,label,input){const row=document.createElement('label');row.className='modelSettingField';row.append(document.createTextNode(label),input);parent.append(row);return input;}
 function select(values,current){const input=document.createElement('select');input.className='select';for(const v of new Set([current,...values])){const o=document.createElement('option');o.value=v;o.textContent=v+(values.includes(v)?'':'（未確認）');input.append(o);}input.value=current;return input;}
 window.imageModelUsesOutline=id=>data.models.find(m=>m.id===id)?.backend==='qwen21';
 window.imageModelSupportsStrength=id=>Boolean(data.models.find(m=>m.id===id)?.capabilities.edit_strength);
 window.imageModelUsesBoxes=id=>data.models.find(m=>m.id===id)?.backend==='flux3';
 function menus(){for(const [id,cap] of [['generationModel','generate'],['editModel','edit']]){const menu=el(id),current=menu.value;menu.replaceChildren();for(const m of data.models.filter(m=>m.capabilities[cap])){const o=document.createElement('option');o.value=m.id;o.textContent=m.label;menu.append(o);}menu.value=data.models.some(m=>m.id===current&&m.capabilities[cap])?current:'qwen21_turbo';}syncModelControls();}
 function payload(model,group,values){const body={id:model.id,files:model.files,sampling:model.sampling};if(model.edit_sampling)body.edit_sampling=model.edit_sampling;body[group]=values;return body;}
 function currentModel(){if(view==='edit')return el('editModel').value;if(view==='explore')return window.exploreParameterModel?.()||el('generationModel').value;if(view==='inspire')return window.inspireParameterModel?.()||el('generationModel').value;return el('generationModel').value;}
 const statuses=new Map(),failures=new Map();let pending=Promise.resolve();
 function enqueue(model,mode,values,key,note,reset=false){
  const task=pending.then(async()=>{
   try{const result=await api('/api/model-settings',{id:model.id,parameter_mode:mode,...(reset?{reset:true}:{parameters:values})});
    data.models=data.models.map(m=>m.id===model.id?result.model:m);failures.delete(key);statuses.delete(key);note.textContent='';
    if(reset){drafts.delete(key);renderParameters();}
   }catch(e){failures.set(key,e.message);statuses.set(key,'保存できません：'+e.message);note.textContent='保存できません：'+e.message;}
  });pending=task;return task;
 }
 window.flushParameterSaves=async()=>{const active=document.activeElement;if(active?.matches('#modelParameters [data-parameter]'))active.dispatchEvent(new Event('change'));await pending;const failure=failures.get(currentModel()+':'+view)||failures.get(currentModel()+':files')||failures.get(currentModel()+':upsamplers');if(failure)throw Error('パラメータを保存できていません：'+failure);};
 function renderParameters(){const model=data.models.find(m=>m.id===currentModel());syncSizeControls(model);const qwen=model?.backend==='qwen21';el('characterGroup').classList.toggle('hidden',!qwen||!['chat','explore','inspire'].includes(view));panel.replaceChildren();panel.hidden=!['chat','edit','explore','inspire'].includes(view);if(panel.hidden)return;
  if(!model){panel.textContent='パラメータを読み込んでいます…';return;}const customWorkflow=model.backend==='user_workflow';if(customWorkflow){const note=document.createElement('p');note.className='sub';note.textContent='新規生成専用。Steps・CFG・モデルファイルは登録JSONの値を使用します。サイズ・seedも入力先を指定した場合のみ変更します。未指定のseedでは同じ結果になることがあります。';panel.append(note);if(!model.bindings?.seed)return;}
  const mode=view,group=mode==='edit'&&model.edit_sampling?'edit_sampling':'sampling',key=model.id+':'+mode;
  const values=drafts.get(key)||{...model[group],...(model.mode_parameters?.[mode]||{})};drafts.set(key,values);
  const heading=document.createElement('div');heading.className='label';heading.textContent=mode==='edit'?'編集パラメータ':'生成パラメータ';panel.append(heading);
  const name=document.createElement('div');name.className='sub';name.textContent=model.label+' ／ このモデル・モード専用。変更は自動保存されます。';panel.append(name);if(!customWorkflow&&model.capabilities.character_reference===false){const hint=document.createElement('div');hint.className='sub';hint.textContent=model.backend==='flux3'?'元画像と指示で編集します。必要に応じて矩形1つで変更位置を指定できます。キャラクターの参照画像は使いません。':'キャラクターの参照画像は使いません。全体img2img・通常のマスク編集に対応しています。';panel.append(hint);}
  const inputs={},note=document.createElement('div');note.className='sub';note.setAttribute('role','status');note.textContent=statuses.get(key)||'';
  const schedule=()=>{enqueue(model,mode,{...values},key,note);};
  const seedMode=document.createElement('select');
  seedMode.className='select';
  for(const [value,label] of [['auto','自動（毎回ランダム）'],['fixed','固定値']]){
   const option=document.createElement('option');option.value=value;option.textContent=label;seedMode.append(option);
  }
  seedMode.value=values.seed==null?'auto':'fixed';
  field(panel,'Seedの設定',seedMode);
  const seedInput=document.createElement('input');
  seedInput.type='number';
  seedInput.min=0;
  seedInput.max=Number.MAX_SAFE_INTEGER;
  seedInput.step=1;
  seedInput.value=values.seed??0;
  seedInput.disabled=seedMode.value==='auto';
  seedInput.dataset.parameter='seed';
  field(panel,'固定Seed',seedInput);
  function saveSeed(){
   const raw=String(seedInput.value).trim();
   values.seed=seedMode.value==='auto'?null:(raw===''?-1:Number(raw));
   schedule();
  }
  seedMode.addEventListener('change',()=>{
   seedInput.disabled=seedMode.value==='auto';
   if(!seedInput.disabled&&String(seedInput.value).trim()==='')seedInput.value=0;
   saveSeed();
  });
  seedInput.addEventListener('change',saveSeed);
  let stepsHint=null;
  for(const [k,v] of Object.entries(values)){if(k==='seed')continue;let input;if(k==='steps'||k==='cfg'){input=document.createElement('input');input.type='number';input.min=k==='steps'?1:0;input.max=k==='steps'?200:30;input.step=k==='steps'?1:0.1;input.value=v;}else if(k==='sigmas'){input=document.createElement('textarea');input.rows=4;input.value=v;}else input=select((k==='sampler_name'?(group==='edit_sampling'?data.edit_samplers:data.samplers):data.schedulers)||[],v);
   input.dataset.parameter=k;inputs[k]=field(panel,labels[k],input);
   const read=()=>{values[k]=k==='steps'||k==='cfg'?(input.value.trim()===''?null:Number(input.value)):input.value;if(stepsHint)stepsHint.textContent='編集ステップ数：'+(String(values.sigmas).split(',').length-1)+'（Sigmasの個数−1、末尾0）';};
   input.addEventListener('input',read);input.addEventListener('change',()=>{read();schedule();});
  }
  if(group==='edit_sampling'){stepsHint=document.createElement('div');stepsHint.className='sub';stepsHint.textContent='編集ステップ数：'+(String(values.sigmas).split(',').length-1)+'（Sigmasの個数−1、末尾0）';panel.append(stepsHint);}
  const reset=button('パラメータを標準に戻す',()=>enqueue(model,mode,null,key,note,true));panel.append(reset,note);
 }
 window.characterForGeneration=()=>data.models.find(m=>m.id===currentModel())?.backend==='qwen21'?selectedCharacterId:'';
 function renderSettings(){content.replaceChildren();for(const model of data.models){const section=document.createElement('section');section.className='modelSettingsCard';const heading=document.createElement('h3');heading.textContent=model.label;const report=document.createElement('p');report.className='sub';report.style.whiteSpace='pre-wrap';report.textContent=model.check?(model.check.ready?'ファイル・必要ノード確認済み':model.check.errors.join('\n')):'未確認';section.append(heading,report);
   if(model.backend==='user_workflow'){renderWorkflowCard(model,section,report);content.append(section);continue;}
   const inputs={};for(const [k,v] of Object.entries(model.files)){const input=select(data.files?.[k]||[],v);input.disabled=!data.connected;inputs[k]=field(section,labels[k],input);}
   if(model.upsamplers)renderUpsamplers(model,section);
   let savedFiles={...model.files};
   for(const input of Object.values(inputs))input.addEventListener('change',()=>{
    const files=Object.fromEntries(Object.entries(inputs).map(([k,v])=>[k,v.value]));report.textContent='確認・保存中…';
    pending=pending.then(async()=>{try{const current=data.models.find(m=>m.id===model.id);const r=await api('/api/model-settings',payload(current,'files',files));data.models=data.models.map(m=>m.id===model.id?r.model:m);savedFiles={...r.model.files};failures.delete(model.id+':files');report.textContent='';}catch(e){failures.set(model.id+':files',e.message);report.textContent='保存できません：'+e.message;}});
   });
   const test=trialButton(model,report,model.backend==='sdxl'?1024:512,'画像モデル設定の動作確認',()=>{
    if(Object.entries(inputs).some(([key,input])=>input.value!==savedFiles[key])||failures.has(model.id+':files')){
     throw Error('ファイル設定の保存に失敗しています。選択を変更して再試行してください。');
    }
   });
   const reset=button('ファイル設定を標準に戻す',async()=>{reset.disabled=true;try{await pending;const r=await api('/api/model-settings',{id:model.id,reset_group:'files'});failures.delete(model.id+':files');failures.delete(model.id+':upsamplers');data.models=data.models.map(m=>m.id===model.id?r.model:m);renderSettings();}catch(e){report.textContent=e.message;reset.disabled=false;}});section.append(reset,test);if(model.custom)section.append(removeButton('追加モデルを削除',async()=>{if(!await appConfirm(model.label+' の登録を削除しますか？ モデルファイルや生成画像は削除しません。'))return;try{await pending;await api('/api/model-settings',{action:'remove_custom',id:model.id});await load();}catch(e){report.textContent=e.message;}}));content.append(section);
  }renderModelRegistration();}
 function renderUpsamplers(model,section){
  const hint=document.createElement('p');hint.className='sub';hint.textContent='プロンプト拡張モデル。新規生成の設定はチャット・探索・ひらめきに適用します。選択すると自動保存されます。';section.append(hint);
  const inputs={},note=document.createElement('p');note.className='sub';note.setAttribute('role','status');
  for(const [mode,label] of [['generate','新規生成用アップサンプラー'],['edit','編集用アップサンプラー']]){
   const current=model.upsamplers[mode]||'';
   const candidates=(data.files?.text_encoder||[]).filter(name=>(model.backend==='flux3'?/flux3_prompt_upsampler/i.test(name):new RegExp('qwen_image_2\\.1_pe_'+(mode==='edit'?'i2i':'t2i'),'i').test(name))&&name.endsWith('.safetensors'));
   const input=select(candidates,current);input.options[0].textContent=current?input.options[0].textContent:'使用しない';
   if(current){const none=document.createElement('option');none.value='';none.textContent='使用しない';input.prepend(none);input.value=current;}
   inputs[mode]=field(section,label,input);
   input.onchange=()=>{
    const ups=Object.fromEntries(Object.entries(inputs).map(([key,el])=>[key,el.value]));
    note.textContent='保存中…';
    pending=pending.then(async()=>{try{const result=await api('/api/model-settings',{id:model.id,upsamplers:ups});data.models=data.models.map(m=>m.id===model.id?result.model:m);failures.delete(model.id+':upsamplers');note.textContent='';}catch(e){failures.set(model.id+':upsamplers',e.message);note.textContent='保存できません：'+e.message;}});
   };
  }
  section.append(note);
 }
 function renderModelRegistration(){
  const box=document.createElement('section');box.className='modelSettingsCard';
  const heading=document.createElement('h3');heading.textContent='モデルを新規登録';
  const reuse=document.createElement('p');reuse.className='sub';
  reuse.textContent='対応済みモデルのワークフロー：同じ構造の派生モデルや量子化版を追加するときに選びます。ワークフローと標準パラメータを引き継ぎます。SDXLは一体型チェックポイント1個、それ以外はモデル本体・テキストエンコーダー・VAEを指定します。';
  const custom=document.createElement('p');custom.className='sub';
  custom.textContent='ワークフローを新規登録：ComfyUIで動作確認したAPI形式JSONを取り込みます。新規生成専用です。プロンプトの入力先と画像出力を選び、必要ならseed・幅・高さを指定します。未指定の項目やモデルファイル、Steps・CFGはJSONの設定を使います。';
  box.append(heading,reuse,custom);
  const bases=data.models.filter(model=>!model.custom);
  const workflow=document.createElement('select');workflow.className='select';
  for(const [value,label] of [['','ワークフローを選択してください'],...bases.map(model=>[model.id,model.label]),['__new_workflow__','ワークフローを新規登録（API形式JSON）']]){
   const option=document.createElement('option');
   option.value=value;
   option.textContent=label;
   workflow.append(option);
  }
  field(box,'ワークフロー選択',workflow);
  const details=document.createElement('div');
  box.append(details);
  workflow.onchange=()=>{
   details.replaceChildren();
   if(workflow.value==='__new_workflow__'){
    renderAddWorkflow(details);
   }else{
    const base=bases.find(model=>model.id===workflow.value);
    if(base)renderAddModel(details,base);
   }
  };
  content.append(box);
 }
 function renderAddModel(box,base){
  const name=document.createElement('input');name.maxLength=80;name.placeholder='例：追加モデル';field(box,'表示名',name);
  const inputs={};
  for(const [key,value] of Object.entries(base.files)){
   inputs[key]=field(box,labels[key],select(data.files?.[key]||[],value));
  }
  const note=document.createElement('p');note.className='sub';note.setAttribute('role','status');
  const add=button('モデルを登録',async()=>{add.disabled=true;note.textContent='ファイル・必要ノードを確認しています…';try{await pending;await api('/api/model-settings',{action:'add_custom',base_id:base.id,label:name.value,files:Object.fromEntries(Object.entries(inputs).map(([k,v])=>[k,v.value]))});await load();state.textContent='モデルを追加しました。各モードのモデル選択で使用できます。';}catch(e){note.textContent=e.message;}finally{add.disabled=!data.connected;}});add.disabled=!data.connected;box.append(add,note);
 }
 function trialButton(model,report,size,instruction,checkFiles){
  const test=button('新規生成を1枚試す',async()=>{
   test.disabled=true;
   let trialProgressTimer;
   try{
    await pending;
    checkFiles?.();
    if(progressTimer!==null)throw Error('実行中の処理が完了してからお試しください。');
    startProgress('お試し生成中…',model.label+' ｜ 1枚');
    trialProgressTimer=progressTimer;
    const prompt='A red ceramic teapot on a wooden table, soft daylight'+
     (checkFiles?', detailed photograph.':'.');
    const result=await api('/api/generate-from-prompt',{
     model:model.id,prompt,width:size,height:size,user_instruction:instruction
    });
    if(!result.image)throw Error('画像が返りませんでした。');
    addImages([result.image]);
    report.textContent='新規生成に成功しました。';
   }catch(e){
    report.textContent=e.message;
   }finally{
    if(trialProgressTimer!==undefined&&progressTimer===trialProgressTimer)stopProgress();
    test.disabled=!data.connected;
   }
  });
  test.disabled=!data.connected;
  return test;
 }
 function renderWorkflowCard(model,section,report){
  const hint=document.createElement('p');hint.className='sub';hint.textContent='新規生成専用 ／ JSON内のモデル・パラメータを使用。変更する場合はJSONを修正して再登録してください。';section.append(hint);
  for(const [name,target] of Object.entries(model.bindings)){const line=document.createElement('div');line.className='sub';line.textContent=({prompt:'プロンプト',seed:'seed（自動／固定を選択可）',width:'幅',height:'高さ'}[name])+': '+target.join(' / ');section.append(line);}
  const test=trialButton(model,report,1024,'ワークフロー動作確認');
  const remove=removeButton('追加モデルを削除',async()=>{if(!await appConfirm(model.label+' の登録を削除しますか？'))return;try{await pending;await api('/api/model-settings',{action:'remove_custom',id:model.id});await load();}catch(e){report.textContent=e.message;}});section.append(test,remove);
 }
 function renderAddWorkflow(box){
  const name=document.createElement('input');name.maxLength=80;field(box,'表示名',name);
  const file=document.createElement('input');file.type='file';file.accept='.json,application/json';field(box,'API形式JSON',file);
  const mappings=document.createElement('div'),note=document.createElement('p');note.className='sub';note.setAttribute('role','status');box.append(mappings,note);let graph=null,inputs={},output=null;
  const add=button('モデルを登録',async()=>{add.disabled=true;try{await pending;const bindings={};for(const [key,input] of Object.entries(inputs))if(input.value)bindings[key]=JSON.parse(input.value);await api('/api/model-settings',{action:'add_workflow',label:name.value,graph,bindings,output_node:output.value});await load();state.textContent='登録しました。新規生成を1枚試して確認してください。';}catch(e){note.textContent=e.message;}finally{add.disabled=!graph||!data.connected;}});add.disabled=true;box.append(add);
  file.onchange=async()=>{graph=null;inputs={};output=null;mappings.replaceChildren();add.disabled=true;try{const selected=file.files[0];if(!selected)return;if(selected.size>5*1024*1024)throw Error('JSONは5MB以下にしてください。');const parsed=JSON.parse(await selected.text());const entries=Object.entries(parsed);if(!entries.length||entries.some(([id,n])=>!n||typeof n.class_type!=='string'||!n.inputs||typeof n.inputs!=='object')||Array.isArray(parsed))throw Error('API形式JSONを選んでください。通常の画面保存JSONは使えません。');graph=parsed;if(!name.value)name.value=selected.name.replace(/\.json$/i,'');
   for(const [key,label] of [['prompt','プロンプト入力先（必須）'],['seed','seed入力先（任意）'],['width','幅の入力先（任意）'],['height','高さの入力先（任意）']]){
    const input=document.createElement('select');input.className='select';const empty=document.createElement('option');empty.value='';empty.textContent=key==='prompt'?'入力先を選択してください':'未指定：JSONの値を使用';input.append(empty);
    const candidates=[];for(const [id,node] of entries)for(const [fieldName,value] of Object.entries(node.inputs))if((key==='prompt'&&typeof value==='string')||(key!=='prompt'&&Number.isSafeInteger(value))){candidates.push({id,node,fieldName,value});}
    candidates.sort((a,b)=>Number(b.fieldName===(key==='prompt'?'text':key))-Number(a.fieldName===(key==='prompt'?'text':key)));
    for(const c of candidates){const o=document.createElement('option');o.value=JSON.stringify([c.id,c.fieldName]);o.textContent=(c.node._meta?.title||c.node.class_type)+' ['+c.id+'] / '+c.fieldName+' = '+String(c.value).slice(0,70);input.append(o);}inputs[key]=field(mappings,label,input);
   }
   output=document.createElement('select');output.className='select';for(const [id,node] of entries)if(['SaveImage','PreviewImage'].includes(node.class_type)){const o=document.createElement('option');o.value=id;o.textContent=(node._meta?.title||node.class_type)+' ['+id+']';output.append(o);}field(mappings,'取得する画像出力',output);if(!output.options.length)throw Error('SaveImageまたはPreviewImageが必要です。');note.textContent='候補のノード名・現在値を確認して選択してください。配線された項目は候補に表示しません。';add.disabled=!data.connected;
  }catch(e){graph=null;note.textContent=e.message;}};
 }
 function load(){
  if(loading)return loading;
  refresh.disabled=true;
  state.textContent='ComfyUIへ問い合わせています…';
  loading=(async()=>{
   try{
    data=await api('/api/model-settings');
    menus();
    state.textContent=data.connected?'接続できました：'+data.url:'接続できません：'+data.url+' ／ '+data.error;
    if(savedConnectionUrl&&data.url&&data.url!==savedConnectionUrl){
     state.textContent='現在の接続先は未確認です。';
    }
    renderSettings();
    renderParameters();
   }catch(e){
    state.textContent=e.message;
   }finally{
    loading=null;
    refresh.disabled=false;
   }
  })();
  return loading;
 }
 el('settingsTab').onclick=()=>{showView('settings');load();};
 window.addEventListener('app-view-change',e=>{view=e.detail;renderParameters();});
 window.addEventListener('parameter-model-change',renderParameters);
 el('generationModel').addEventListener('change',renderParameters);el('editModel').addEventListener('change',renderParameters);
 api('/api/comfy/connection').then(result=>{
  connectionUrl.value=savedConnectionUrl=result.url;
  comfyUnload.checked=savedComfyUnload=Boolean(result.unload_after_generation);
 }).catch(e=>{
  connectionReport.textContent=e.message;
 }).finally(()=>{connectionUrl.disabled=comfyUnload.disabled=false;});
 api('/api/models').then(d=>{if(!data.models.length){data.models=d.models;menus();renderParameters();}load();}).catch(e=>{state.textContent=e.message;});
})();
