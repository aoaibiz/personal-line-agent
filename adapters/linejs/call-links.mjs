/** Call-link management only; never route tokens, microphones or automatic calls. */
const safeId=x=>typeof x==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(x);
export async function callLinks(client) {
 const response=await client.base.call.getGroupCallUrls({});
 if(!Array.isArray(response?.urls)||response.urls.length>1000) throw Error('unexpected_schema');
 return {items:response.urls.map(row=>{
  if(!safeId(row.urlId)||typeof row.title!=='string'||row.title.length>1000) throw Error('unexpected_schema');
  return {url_id:row.urlId,title:row.title,created_at:String(row.createdTimeMillis)};
 })};
}
export async function callLinkContext(client,kind,payload) {
 if(!['call_link_create','call_link_update','call_link_delete'].includes(kind)) throw Error('input_invalid');
 if(kind==='call_link_create') return {owner:client.base.profile.mid};
 if(!safeId(payload.url_id)) throw Error('input_invalid');
 const rows=(await callLinks(client)).items.filter(x=>x.url_id===payload.url_id);
 if(rows.length!==1) throw Error('call_link_not_owned');
 return {owner:client.base.profile.mid,...rows[0]};
}
export async function executeCallLink(client,kind,payload,expected) {
 const context=await callLinkContext(client,kind,payload);
 if(JSON.stringify(context)!==JSON.stringify(expected)) throw Error('call_link_changed');
 if(kind!=='call_link_delete'&&(typeof payload.title!=='string'||payload.title.length>100||!payload.title.length)) throw Error('input_invalid');
 if(kind==='call_link_create') {
  const r=await client.base.call.createGroupCallUrl({request:{title:payload.title}});
  if(!safeId(r?.url?.urlId)) throw Error('unexpected_schema');
  return {id:r.url.urlId};
 }
 if(kind==='call_link_update') await client.base.call.updateGroupCallUrl({request:{urlId:payload.url_id,targetAttribute:{title:payload.title}}});
 if(kind==='call_link_delete') await client.base.call.deleteGroupCallUrl({request:{urlId:payload.url_id}});
 return {id:payload.url_id,applied:true};
}
