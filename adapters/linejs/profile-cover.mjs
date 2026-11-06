/** Own cover contract: CHRLINE timeline.py/object.py, 3ee3206 (BSD-3-Clause).
 * Current read confirmed code0 / myhome namespace c; writes require owner approval.
 */
const objectId=x=>typeof x==='string'&&/^[A-Za-z0-9_-]{1,200}$/.test(x);
async function request(client,method,body) {
 await client.base.timeline.initTimeline();
 const query=method==='GET'?'?'+new URLSearchParams({homeId:client.base.profile.mid}):'';
 const response=await client.base.fetch('https://'+client.base.endpoint+'/hm/api/v1/home/cover.json'+query,{
  method:'POST',headers:{...client.base.timeline.timelineHeaders,'x-lhm':method,'content-type':'application/json'},
  body:method==='GET'?new Uint8Array():JSON.stringify(body)
 });
 if(!response.ok) throw Error('cover_upstream_failed');
 const data=await response.json();
 if(data?.code!==0||(method==='GET'&&(!data.result||typeof data.result!=='object'))) throw Error('cover_upstream_failed');
 return data.result;
}
export async function coverContext(client) {
 const row=await request(client,'GET');
 const cover=row.coverObsInfo;
 // No guessed empty-cover state: require the schema observed on the real account.
 if(!cover||cover.serviceName!=='myhome'||cover.obsNamespace!=='c'||!objectId(cover.objectId)) throw Error('cover_schema_unverified');
 return {owner:client.base.profile.mid,object_id:cover.objectId};
}
export async function setCover(client,p,expected) {
 if(typeof p.data!=='string'||p.data.length>7*1024*1024||!['image/png','image/jpeg'].includes(p.content_type)) throw Error('input_invalid');
 const bytes=Buffer.from(p.data,'base64');
 if(bytes.length<8||bytes.length>5*1024*1024||bytes.toString('base64')!==p.data) throw Error('input_invalid');
 const png=bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]));
 const jpeg=bytes[0]===255&&bytes[1]===216&&bytes[2]===255;
 if(!(p.content_type==='image/png'?png:jpeg)) throw Error('input_invalid');
 const current=await coverContext(client);
 if(current.owner!==expected?.owner||current.object_id!==expected?.object_id) throw Error('cover_changed');
 if(typeof p.upload_id!=='string'||! /^[a-f0-9]{32}$/.test(p.upload_id)) throw Error('input_invalid');
 const id=p.upload_id;
 const headers=client.base.timeline.timelineHeaders;
 const channel=Object.entries(headers).find(([key])=>key.toLowerCase()==='x-line-channeltoken')?.[1];
 if(typeof channel!=='string'||!channel) throw Error('cover_upstream_failed');
 // Correct cover namespace; never treat the SDK's myhome/h upload as applied.
 const uploaded=await client.base.obs.uploadObjectForService({data:new Blob([bytes],{type:p.content_type}),oType:'image',obsPath:'myhome/c/'+id,addHeaders:{'X-Line-ChannelToken':channel}});
 if(uploaded.objId!==id||typeof uploaded.objHash!=='string'||!uploaded.objHash) throw Error('cover_upload_unconfirmed');
 let after;
 try {
  await request(client,'POST',{homeId:current.owner,storyShare:false,meta:{},coverObjectId:id});
  after=await coverContext(client);
 } catch { throw Error('cover_apply_unconfirmed'); }
 if(after.owner!==current.owner||after.object_id!==id) throw Error('cover_apply_unconfirmed');
 return {uploaded:true,applied:true,readback_verified:true};
}
