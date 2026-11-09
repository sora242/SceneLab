(()=>{
const el=id=>document.getElementById(id),open=el('galleryImportOpen'),modal=el('galleryImportModal'),result=el('galleryImportResult');
const fileInput=el('galleryImportFileInput'),folderInput=el('galleryImportFolderInput');let importing=false;
const supported=/\.(png|jpe?g|webp|gif|bmp|avif)$/i;
function available(){if(importing)return false;if(send.disabled||window.explorationBusy||window.inspirationBusy){status.textContent='実行中の処理が終わってからインポートしてください。';return false;}return true;}
open.onclick=()=>{if(!available())return;result.textContent='';modal.classList.add('open');};
function close(){if(!importing)modal.classList.remove('open');}
el('galleryImportClose').onclick=close;modal.onclick=e=>{if(e.target===modal)close();};modal.onkeydown=e=>{if(e.key==='Escape')close();};
el('galleryImportFiles').onclick=()=>{fileInput.value='';fileInput.click();};el('galleryImportFolder').onclick=()=>{folderInput.value='';folderInput.click();};
function readDataURL(blob){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('ファイルを読み取れませんでした。'));reader.readAsDataURL(blob);});}
async function pngData(file){
 if(file.type==='image/png'||/\.png$/i.test(file.name))return readDataURL(file.type==='image/png'?file:new Blob([file],{type:'image/png'}));
 const url=URL.createObjectURL(file);
 try{
  const image=await new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error('この画像形式を読み取れませんでした。'));img.src=url;});
  const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
  if(!canvas.width||!canvas.height)throw new Error('画像サイズを読み取れませんでした。');
  const ctx=canvas.getContext('2d');if(!ctx)throw new Error('画像を変換できませんでした。');ctx.drawImage(image,0,0);
  const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw new Error('画像をPNGに変換できませんでした。');return await readDataURL(blob);
 }finally{URL.revokeObjectURL(url);}
}
async function importFiles(selection){
 if(!available())return;const all=Array.from(selection||[]),files=all.filter(file=>supported.test(file.name));if(!files.length){result.textContent='対応する画像がありません。PNG・JPEG・WebP・GIF・BMP・AVIFを選択してください。';return;}
 importing=true;window.galleryImportBusy=true;[open,send,directSend,consult,editSend,el('galleryImportFiles'),el('galleryImportFolder'),el('galleryImportClose')].forEach(button=>button.disabled=true);
 let added=0;const errors=[];
 startProgress('画像をインポートしています…',files.length+'枚');progressCancel.classList.remove('show');
 try{
  for(let i=0;i<files.length;i++){
   const file=files[i];const message=`インポート中 ${i+1} / ${files.length}：${file.name}`;result.textContent=message;updateProgress(message);
   try{
    const image_data=await pngData(file);const response=await fetch('/api/gallery/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:file.name,image_data})});const data=await readApiResponse(response);if(!response.ok)throw new Error(data.error||'保存に失敗しました。');addImages([data.image]);added++;
   }catch(e){errors.push(file.name+'：'+e.message);}
  }
  const summary=`${added}枚をギャラリーに追加しました。`+(errors.length?` ${errors.length}件失敗。`:'')+(all.length>files.length?` 画像以外の${all.length-files.length}件は対象外です。`:'');
  result.textContent=summary+(errors.length?'\n\n'+errors.slice(0,5).join('\n'):'');status.textContent=summary;
  if(added)showView('chat');
 }finally{
  importing=false;window.galleryImportBusy=false;stopProgress();[open,send,directSend,consult,editSend,el('galleryImportFiles'),el('galleryImportFolder'),el('galleryImportClose')].forEach(button=>button.disabled=false);
 }
}
fileInput.onchange=()=>importFiles(fileInput.files);folderInput.onchange=()=>importFiles(folderInput.files);
window.addEventListener('beforeunload',e=>{if(importing){e.preventDefault();e.returnValue='';}});
})();
