// Application confirmation is independent of browser confirm() suppression.
let activeAppConfirmation=null;
function appConfirm(message,options={}){
 if(activeAppConfirmation)return Promise.resolve(false);
 return new Promise(resolve=>{
  const previous=document.activeElement;
  const dialog=document.createElement('dialog');dialog.className='appConfirmDialog';
  dialog.setAttribute('aria-labelledby','appConfirmTitle');dialog.setAttribute('aria-describedby','appConfirmMessage');
  const title=document.createElement('h2');title.id='appConfirmTitle';title.textContent='確認';
  const text=document.createElement('p');text.id='appConfirmMessage';text.textContent=message;
  const actions=document.createElement('div');actions.className='appConfirmActions';
  const cancel=document.createElement('button');cancel.type='button';cancel.className='mutedSmall';cancel.textContent='キャンセル';cancel.autofocus=true;
  const accept=document.createElement('button');accept.type='button';accept.className='dangerSmall';accept.textContent=options.confirmLabel||'削除する';
  actions.append(cancel,accept);dialog.append(title,text,actions);document.body.append(dialog);
  activeAppConfirmation=dialog;
  let settled=false;
  function finish(confirmed){
   if(settled)return;settled=true;activeAppConfirmation=null;
   if(dialog.open)dialog.close();dialog.remove();
   if(previous?.isConnected)previous.focus();
   resolve(confirmed);
  }
  cancel.onclick=()=>finish(false);accept.onclick=()=>finish(true);
  dialog.addEventListener('cancel',event=>{event.preventDefault();finish(false);});
  dialog.addEventListener('close',()=>finish(false));
  try{dialog.showModal();cancel.focus();}catch(_e){finish(false);}
 });
}
const appConfirmStyle=document.createElement('style');
appConfirmStyle.textContent='.appConfirmDialog{width:min(440px,calc(100vw - 40px));max-height:80vh;box-sizing:border-box;overflow:auto;background:#171d26;color:#e7edf6;border:1px solid #46536a;border-radius:14px;padding:24px;box-shadow:0 18px 70px #0008}.appConfirmDialog::backdrop{background:#0009}.appConfirmDialog h2{margin:0 0 14px;font-size:18px}.appConfirmDialog p{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7;margin:0 0 22px}.appConfirmActions{display:flex;justify-content:flex-end;gap:12px}.appConfirmActions button{min-height:42px;padding:10px 18px;cursor:pointer}.appConfirmActions .dangerSmall{background:#a52e40;color:#fff;border:1px solid #e26a7d}.appConfirmActions button:focus-visible{outline:2px solid #85baff;outline-offset:3px}';
document.head.append(appConfirmStyle);

// Opt-in completion sound and background desktop notifications.
let completionNotificationsEnabled=false;
let completionAudio=null;
const completionNotificationButton=document.getElementById('completionNotifications');
try{completionNotificationsEnabled=localStorage.getItem('flashnext_completion_notifications')==='on';}catch(_e){}
function updateCompletionNotificationButton(){
 completionNotificationButton.textContent=completionNotificationsEnabled?'通知：ON':'通知：OFF';
 completionNotificationButton.setAttribute('aria-pressed',String(completionNotificationsEnabled));
 const desktop='Notification' in window&&Notification.permission==='granted';
 completionNotificationButton.title=completionNotificationsEnabled?(desktop?'完了時に通知音。画面を見ていないときはデスクトップ通知も表示します。':'完了時に通知音を鳴らします。デスクトップ通知はブラウザの通知許可が必要です。'):'クリックして画像生成の完了通知を有効にします。';
}
function unlockCompletionAudio(){
 if(!completionNotificationsEnabled)return;
 try{const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return;if(!completionAudio)completionAudio=new Audio();if(completionAudio.state==='suspended')completionAudio.resume().catch(()=>{});}catch(_e){}
}
completionNotificationButton.onclick=async()=>{
 completionNotificationsEnabled=!completionNotificationsEnabled;
 try{localStorage.setItem('flashnext_completion_notifications',completionNotificationsEnabled?'on':'off');}catch(_e){}
 updateCompletionNotificationButton();
 if(!completionNotificationsEnabled)return;
 unlockCompletionAudio();
 if('Notification' in window&&Notification.permission==='default'){
  try{await Notification.requestPermission();}catch(_e){}
 }
 updateCompletionNotificationButton();
};
document.addEventListener('pointerdown',unlockCompletionAudio,{passive:true});
document.addEventListener('keydown',unlockCompletionAudio,{passive:true});
function notifyImageCompletion(message){
 if(!completionNotificationsEnabled)return;
 // Notification failures must never affect image generation.
 try{
  unlockCompletionAudio();
  if(completionAudio&&completionAudio.state==='running'){
   const audio=completionAudio,now=audio.currentTime;
   [880,1174.66].forEach((frequency,index)=>{
    const osc=audio.createOscillator(),gain=audio.createGain(),at=now+index*0.18;
    osc.type='sine';osc.frequency.value=frequency;
    gain.gain.setValueAtTime(0,at);gain.gain.linearRampToValueAtTime(0.09,at+0.015);gain.gain.exponentialRampToValueAtTime(0.001,at+0.25);
    osc.connect(gain);gain.connect(audio.destination);osc.start(at);osc.stop(at+0.27);
   });
  }
 }catch(_e){}
 try{
  if('Notification' in window&&Notification.permission==='granted'&&(document.hidden||!document.hasFocus())){
   const notice=new Notification('SceneLab',{body:message,tag:'flashnext-image-completion',silent:true});
   notice.onclick=()=>{window.focus();notice.close();};
  }
 }catch(_e){}
}
updateCompletionNotificationButton();
