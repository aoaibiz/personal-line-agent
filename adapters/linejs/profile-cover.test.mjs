import test from 'node:test';
import assert from 'node:assert/strict';
import {coverContext,setCover} from './profile-cover.mjs';
function fixture() {
 let id='previous',uploads=0,changes=0;
 const client={base:{profile:{mid:'u'+'1'.repeat(32)},endpoint:'legy-jp.line-apps.com',timeline:{initTimeline:async()=>{},timelineHeaders:{'X-Line-ChannelToken':'synthetic'}},obs:{uploadObjectForService:async p=>{uploads++;assert.match(p.obsPath,/^myhome\/c\/[a-f0-9]{32}$/);assert.equal(p.addHeaders['X-Line-ChannelToken'],'synthetic');return {objId:p.obsPath.split('/').at(-1),objHash:'synthetic'};}},fetch:async(url,options)=>{
  assert.equal(options.method,'POST');
  assert.ok(url.includes('/hm/api/v1/home/cover.json'));
  if(options.headers['x-lhm']==='POST') {const body=JSON.parse(options.body);assert.equal(body.homeId,client.base.profile.mid);assert.equal(body.storyShare,false);assert.deepEqual(body.meta,{});id=body.coverObjectId;changes++;return Response.json({code:0});}
  return Response.json({code:0,result:{coverObsInfo:{serviceName:'myhome',obsNamespace:'c',objectId:id}}});
 }}};
 return {client,counts:()=>({uploads,changes})};
}
const payload={upload_id:'2'.repeat(32),data:Buffer.from([137,80,78,71,13,10,26,10,1]).toString('base64'),content_type:'image/png'};
test('own cover applies correct namespace without story and verifies readback',async()=>{
 const f=fixture();const before=await coverContext(f.client);
 const result=await setCover(f.client,payload,before);
 assert.deepEqual(result,{uploaded:true,applied:true,readback_verified:true});
 assert.deepEqual(f.counts(),{uploads:1,changes:1});
});
test('changed cover or foreign owner prevents upload',async()=>{
 for(const expected of [{owner:'foreign',object_id:'previous'},{owner:'u'+'1'.repeat(32),object_id:'stale'}]) {
  const f=fixture();await assert.rejects(setCover(f.client,payload,expected),/cover_changed/);assert.equal(f.counts().uploads,0);
 }
});
test('upload-only and failed association are not applied success',async()=>{
 const f=fixture();const before=await coverContext(f.client);const original=f.client.base.fetch;
 f.client.base.fetch=async (u,o)=>o.headers['x-lhm']==='POST'?Response.json({code:500}):original(u,o);
 await assert.rejects(setCover(f.client,payload,before),/cover_apply_unconfirmed/);
 assert.equal(f.counts().uploads,1);
});
