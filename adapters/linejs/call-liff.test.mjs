import test from 'node:test';
import assert from 'node:assert/strict';
import {callLinks,callLinkContext,executeCallLink} from './call-links.mjs';
import {validateLiff,shareLiff,tokenWithoutConsent} from './liff-share.mjs';
const owner='u'+'1'.repeat(32),home='c'+'2'.repeat(32);
test('call listing strips routes and tokens, update has exact targetAttribute object',async()=>{
 const calls=[];const row={urlId:'synthetic-link',title:'fixture',createdTimeMillis:1,accessToken:'secret',route:{token:'secret'}};
 const client={base:{profile:{mid:owner},call:{getGroupCallUrls:async()=>({urls:[row]}),updateGroupCallUrl:async a=>calls.push(a)}}};
 assert.deepEqual(await callLinks(client),{items:[{url_id:'synthetic-link',title:'fixture',created_at:'1'}]});
 const p={url_id:'synthetic-link',title:'new'};
 await executeCallLink(client,'call_link_update',p,await callLinkContext(client,'call_link_update',p));
 assert.deepEqual(calls,[{request:{urlId:'synthetic-link',targetAttribute:{title:'new'}}}]);
 const ctx=await callLinkContext(client,'call_link_delete',p);row.title='changed';
 await assert.rejects(executeCallLink(client,'call_link_delete',p,ctx),/changed/);
 await assert.rejects(callLinkContext(client,'call_link_delete',{url_id:'foreign'}),/not_owned/);
});
test('call create unwraps the exact generated response without raw data',async()=>{
 const client={base:{profile:{mid:owner},call:{createGroupCallUrl:async()=>({url:{urlId:'new-link'},token:'secret'})}}};
 assert.deepEqual(await executeCallLink(client,'call_link_create',{title:'x'}, {owner}),{id:'new-link'});
});
test('LIFF forces correct app/recipient and never grants consent or returns tokens',async()=>{
 let seen;const client={base:{liff:{sendLiff:async args=>{seen=args;return {accessToken:'secret'};}}}};
 const p={home,liff_id:'1234567890-abc',messages:[{type:'text',text:'fixture'}]};
 assert.deepEqual(await shareLiff(client,p),{accepted:true});
 assert.equal(client.base.liff.liffId,p.liff_id);
 assert.deepEqual(seen,{to:home,messages:p.messages,tryConsent:false,forceIssue:true});
});
test('LIFF rejects malformed targets, secret-bearing URL and arbitrary message payload',()=>{
 const p={home,liff_id:'1234567890-abc',messages:[{type:'text',text:'x'}]};
 assert.throws(()=>validateLiff({...p,home:owner}),/input_invalid/);
 assert.throws(()=>validateLiff({...p,messages:[{type:'text',text:'x',token:'x'}]}),/input_invalid/);
 assert.throws(()=>validateLiff({...p,messages:[{type:'image',originalContentUrl:'https://user:password@example.com/x',previewImageUrl:'https://example.com/x'}]}),/input_invalid/);
});

test('consent-required survives classification without granting permissions',async()=>{
 const service={issueLiffView:async()=>{throw {data:{liffException:{code:'CONSENT_REQUIRED',payload:{secret:'never returned'}}}}}};
 await assert.rejects(tokenWithoutConsent(service,{chatMid:home,liffId:'1234567890-abc'}),/^Error: liff_consent_required$/);
});
