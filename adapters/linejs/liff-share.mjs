/** LIFF shares never grant consent automatically or return account tokens. */
export function validateLiff(p) {
 if(!/^c[0-9a-f]{32}$/.test(p?.home)||!/^\d{5,20}-[A-Za-z0-9_-]{1,100}$/.test(p?.liff_id)||!Array.isArray(p?.messages)||p.messages.length<1||p.messages.length>5) throw Error('input_invalid');
 if(JSON.stringify(p.messages).length>50000) throw Error('input_invalid');
 for(const m of p.messages) {
  if(!m||typeof m!=='object'||Array.isArray(m)) throw Error('input_invalid');
  const fields={text:['type','text'],sticker:['type','packageId','stickerId'],image:['type','originalContentUrl','previewImageUrl']};
  const allowed=fields[m.type];
  if(!allowed||Object.keys(m).some(k=>!allowed.includes(k))||allowed.some(k=>typeof m[k]!=='string')) throw Error('input_invalid');
  if(m.type==='text'&&(!m.text.length||m.text.length>5000)) throw Error('input_invalid');
  if(m.type==='sticker'&&(!/^\d{1,30}$/.test(m.packageId)||!/^\d{1,30}$/.test(m.stickerId))) throw Error('input_invalid');
  if(m.type==='image') for(const key of ['originalContentUrl','previewImageUrl']) {
   const u=new URL(m[key]); if(u.protocol!=='https:'||u.username||u.password||m[key].length>2000) throw Error('input_invalid');
  }
 }
}
export async function tokenWithoutConsent(service,options) {
 try {
  const view=await service.issueLiffView({chatMid:options.chatMid,liffId:options.liffId,lang:'ja_JP'});
  if(typeof view?.accessToken!=='string'||!view.accessToken) throw Error('liff_connection_failed');
  return view.accessToken;
 } catch(e) {
  const code=e?.data?.liffException?.code;
  if(code==='CONSENT_REQUIRED') throw Error('liff_consent_required');
  throw Error('liff_connection_failed');
 }
}
export async function shareLiff(client,p) {
 validateLiff(p);
 const service=client.base.liff;
 service.liffId=p.liff_id;
 const original=service.getLiffToken;
 // SDK's helper erases consent errors. Use its read-only issue method, with no
 // permission grant route; keep the SDK's exact share HTTP contract.
 service.getLiffToken=options=>tokenWithoutConsent(service,options);
 try {
  await service.sendLiff({to:p.home,messages:p.messages,tryConsent:false,forceIssue:true});
  return {accepted:true};
 } finally { service.getLiffToken=original; }
}
