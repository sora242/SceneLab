/* Region guides: one contrasting color per edit; no image dependencies. */
(function(root){
 function create(source,paint,width,height){
  const count=width*height;
  if(!width||!height||source.length!==count*4||paint.length!==count*4)throw Error('画像サイズが一致しません。');
  const candidates=[{name:'red',rgb:[255,0,0]},{name:'blue',rgb:[0,0,255]},{name:'green',rgb:[0,255,0]}];
  const sums=[0,0,0],dist=new Uint32Array(count);let selected=0;
  for(let i=0;i<count;i++){
   if(paint[i*4+3]<=8)continue;
   selected++;
   for(let c=0;c<3;c++)for(let k=0;k<3;k++){const delta=source[i*4+k]-candidates[c].rgb[k];sums[c]+=delta*delta;}
   const x=i%width,y=Math.floor(i/width);dist[i]=Math.min(x+1,y+1,width-x,height-y);
  }
  if(!selected)throw Error('編集したい場所を塗ってください。');
  let choice=0;for(let c=1;c<3;c++)if(sums[c]>sums[choice])choice=c;
  // Manhattan distance to the unpainted area, including the image boundary.
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){const i=y*width+x;if(!dist[i])continue;if(x)dist[i]=Math.min(dist[i],dist[i-1]+1);if(y)dist[i]=Math.min(dist[i],dist[i-width]+1);}
  for(let y=height-1;y>=0;y--)for(let x=width-1;x>=0;x--){const i=y*width+x;if(!dist[i])continue;if(x+1<width)dist[i]=Math.min(dist[i],dist[i+1]+1);if(y+1<height)dist[i]=Math.min(dist[i],dist[i+width]+1);}
  const pixels=new Uint8ClampedArray(source),thickness=Math.max(2,Math.min(6,Math.round(Math.min(width,height)/300)));
  for(let i=0;i<count;i++)if(dist[i]>0&&dist[i]<=thickness){pixels.set(candidates[choice].rgb,i*4);pixels[i*4+3]=255;}
  return {pixels,color:candidates[choice].name};
 }
 function rectangle(a,b,width,height){
  const clamp=(v,max)=>Math.max(0,Math.min(max,v));
  const x1=clamp(a.x,width),x2=clamp(b.x,width),y1=clamp(a.y,height),y2=clamp(b.y,height);
  if(!width||!height||Math.abs(x2-x1)<width/1000||Math.abs(y2-y1)<height/1000)return null;
  return {x:Math.min(x1,x2)/width,y:Math.min(y1,y2)/height,width:Math.abs(x2-x1)/width,height:Math.abs(y2-y1)/height};
 }
 root.RegionOutline={create,rectangle};
 if(typeof module!=='undefined')module.exports=root.RegionOutline;
})(typeof window!=='undefined'?window:globalThis);
