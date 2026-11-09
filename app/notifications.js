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
