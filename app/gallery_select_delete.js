/* Multi-select deletion reuses the existing persistent deletion API. */
(() => {
 const cleanup=document.getElementById('deleteNonFavorites');
 const open=document.createElement('button');open.id='selectGalleryDelete';open.type='button';open.className='galleryCleanup';open.textContent='選択して削除';open.setAttribute('aria-pressed','false');cleanup.before(open);
 const toolbar=document.createElement('div');toolbar.className='gallerySelectionBar';toolbar.hidden=true;
 const remove=document.createElement('button');remove.type='button';remove.className='dangerSmall';
 const cancel=document.createElement('button');cancel.type='button';cancel.className='secondary';cancel.textContent='キャンセル';
 const notice=document.createElement('div');notice.className='sub';notice.setAttribute('role','status');
 toolbar.append(remove,cancel,notice);gallery.before(toolbar);
 const style=document.createElement('style');style.textContent=`.galleryHeader{flex-wrap:wrap}.galleryHeaderActions{flex-wrap:wrap}.gallerySelectionBar{margin:0 0 12px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}.gallerySelectionBar[hidden]{display:none}.gallerySelectionBar .sub{width:100%}.gallery.selecting .thumb[data-gallery-id]::after{content:'✓';position:absolute;left:8px;top:8px;width:24px;height:24px;border:2px solid #d5deec;border-radius:6px;background:#111c;color:transparent;text-align:center;line-height:23px;pointer-events:none}.gallery.selecting .thumb.deleteSelected{border-color:#ff8d91;box-shadow:0 0 0 1px #ff8d91}.gallery.selecting .thumb.deleteSelected::after{background:#b43d4a;color:#fff}.gallery.selecting .favoriteBadge{top:40px;pointer-events:none}.gallery.selecting .thumbMenu{pointer-events:none}.gallery.selecting .thumb:not([data-gallery-id]){opacity:.5}`;document.head.append(style);
 let active=false,busy=false;const selected=new Set();
 function render(){toolbar.hidden=!active;gallery.classList.toggle('selecting',active);open.setAttribute('aria-pressed',String(active));open.textContent=active?'選択中':'選択して削除';open.disabled=busy;cancel.disabled=busy;remove.disabled=busy||!selected.size;remove.textContent='選択した '+selected.size+'枚を削除';cleanup.disabled=active||busy;for(const thumb of gallery.querySelectorAll('.thumb'))thumb.classList.toggle('deleteSelected',selected.has(thumb.dataset.galleryId));}
 function finish(){active=false;selected.clear();notice.textContent='';render();}
 open.onclick=()=>{if(busy)return;if(active){finish();return;}active=true;notice.textContent='削除したい画像をクリックしてください。★付き画像も選択できます。';render();};cancel.onclick=finish;
 gallery.addEventListener('click',e=>{if(!active)return;e.preventDefault();e.stopImmediatePropagation();if(busy)return;const thumb=e.target.closest('.thumb'),id=thumb?.dataset.galleryId;if(!id||!galleryItems.has(id))return;if(selected.has(id))selected.delete(id);else selected.add(id);render();},true);
 remove.onclick=async()=>{if(busy||!selected.size)return;const ids=[...selected].filter(id=>galleryItems.has(id));if(!ids.length){finish();return;}const stars=ids.filter(id=>galleryItems.get(id).favorite).length;if(!confirm('選択した画像 '+ids.length+'枚'+(stars?'（★付き '+stars+'枚を含む）':'')+'を完全に削除しますか？'))return;
  busy=true;render();let deleted=0;const failed=[];
  try{for(const id of ids){notice.textContent='削除中… '+(deleted+failed.length+1)+' / '+ids.length;
    try{const r=await fetch('/api/gallery/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_id:id})});const result=await readApiResponse(r);if(!r.ok)throw Error(result.error||'削除できませんでした');
     gallery.querySelectorAll('.thumb').forEach(t=>{if(t.dataset.galleryId===id)t.remove();});galleryItems.delete(id);selected.delete(id);clearSelectedIfDeleted(id);if(promptModalItem?.info?.gallery_id===id)closePromptModal();totalImages=Math.max(0,totalImages-1);deleted++;
    }catch(e){failed.push(e.message);}
   }
  }finally{busy=false;galleryCount.textContent=totalImages+'枚';emptyGallery.style.display=totalImages?'none':'block';refreshGalleryCopyButtons();if(!failed.length)finish();else{notice.textContent=deleted+'枚削除、'+failed.length+'枚失敗：'+failed[0];render();}status.textContent=deleted+'枚を削除しました。'+(failed.length?' '+failed.length+'枚は削除できませんでした。':'');}
 };
})();
