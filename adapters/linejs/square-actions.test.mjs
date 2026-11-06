import test from 'node:test';
import assert from 'node:assert/strict';
import {squareContext,executeSquare} from './square-actions.mjs';
const target='m'+'1'.repeat(32),square='s'+'2'.repeat(32),owner='p'+'3'.repeat(32);
function fixture() {
 const calls=[];
 const chat={squareChatMid:target,squareMid:square,state:'ALIVE',name:'fixture',squareChatRevision:1};
 const membership={squareMemberMid:owner,squareChatMid:target,membershipState:'JOINED',revision:2};
 const community={square:{mid:square},myMembership:{squareMid:square,squareMemberMid:owner,membershipState:'JOINED',role:'ADMIN'}};
 const message={id:'123',to:target,from:owner,text:'fixture',contentType:0};
 const api={getSquareChat:async()=>({squareChat:chat,squareChatMember:membership}),getSquare:async()=>community,
 fetchSquareChatEvents:async()=>({events:[{type:'RECEIVE_MESSAGE',payload:{receiveMessage:{squareMessage:{message}}}}]}),
 sendMessage:async args=>{calls.push(args);return {createdSquareMessage:{message}};},
 unsendMessage:async args=>calls.push(args),markAsRead:async args=>calls.push(args),reactToMessage:async args=>calls.push(args),leaveSquareChat:async args=>calls.push(args)};
 return {client:{base:{square:api,getReqseq:async()=>42},getSquareChat:async()=>({updateName:async name=>calls.push({name})})},calls,chat,membership,community,message};
}
test('Square identity and membership are bound across chat and community',async()=>{
 const f=fixture(); const p={target};
 assert.equal((await squareContext(f.client,'square_leave_chat',p)).member,owner);
 f.community.myMembership.squareMemberMid='p'+'9'.repeat(32);
 await assert.rejects(squareContext(f.client,'square_leave_chat',p),/not_member/);
 assert.equal(f.calls.length,0);
});
test('stale context and foreign sender never execute',async()=>{
 const f=fixture();const p={target,message_id:'123'};
 const ctx=await squareContext(f.client,'square_unsend',p);
 f.message.text='changed';
 await assert.rejects(executeSquare(f.client,'square_unsend',p,ctx),/context_changed/);
 f.message.from='p'+'4'.repeat(32);
 await assert.rejects(squareContext(f.client,'square_unsend',p),/own_message_required/);
 assert.equal(f.calls.length,0);
});
test('one send, exact reaction sequence and leave revision, no goodbye',async()=>{
 const f=fixture();
 for(const [kind,p] of [['square_send_text',{target,text:'fixture',reply_to:'123'}],['square_react',{target,message_id:'123',reaction:'NICE'}],['square_leave_chat',{target}]]){
  await executeSquare(f.client,kind,p,await squareContext(f.client,kind,p));
 }
 assert.equal(f.calls.length,3);
 assert.deepEqual(f.calls[0],{squareChatMid:target,text:'fixture',relatedMessageId:'123'});
 assert.equal(f.calls[1].request.reqSeq,42);
 assert.equal(f.calls[2].request.sayGoodbye,false);
 assert.equal(f.calls[2].request.squareChatMemberRevision,2n);
});
test('ordinary user cannot rename, and normal LINE target is rejected',async()=>{
 const f=fixture();f.community.myMembership.role='MEMBER';
 await assert.rejects(squareContext(f.client,'square_rename_chat',{target,name:'x'}),/role_required/);
 await assert.rejects(squareContext(f.client,'square_send_text',{target:'c'+'1'.repeat(32),text:'x'}),/input_invalid/);
});

test('community operations preserve identity, approval state and role',async()=>{
 const f=fixture();f.client.base.profile={mid:'u'+'7'.repeat(32)};
 Object.assign(f.community.square,{state:'ALIVE',name:'synthetic',revision:4,joinMethod:{type:'NONE'}});
 let count=0;
 f.client.base.square.joinSquare=async p=>{count++;assert.equal(p.displayName,'new');return {square:{mid:square},squareMember:{squareMid:square,squareMemberMid:owner,membershipState:'JOIN_REQUESTED'}};};
 f.client.base.square.leaveSquare=async p=>{count++;assert.equal(p.request.squareMid,square);};
 f.client.getSquare=async id=>({updateName:async name=>{count++;assert.equal(id,square);assert.equal(name,'new');}});
 for(const kind of ['square_leave','square_rename']) {const p={target:square,name:'new'};await executeSquare(f.client,kind,p,await squareContext(f.client,kind,p));}
 await assert.rejects(squareContext(f.client,'square_join',{target:square}),/state_conflict/);
 f.community.myMembership=null;
 const p={target:square,display_name:'new',message:''};
 const result=await executeSquare(f.client,'square_join',p,await squareContext(f.client,'square_join',p));
 assert.equal(result.joined,false);assert.equal(result.membership_state,'JOIN_REQUESTED');assert.equal(count,3);
});
test('create passes explicit public visibility and does not hide uncertain response',async()=>{
 const f=fixture();f.client.base.profile={mid:'u'+'7'.repeat(32)};
 f.client.base.square.createSquare=async p=>{assert.equal(p.searchable,false);assert.equal(p.squareJoinMethodType,'NONE');return {square:{mid:square},creator:{squareMid:square,squareMemberMid:owner,role:'ADMIN'}};};
 const p={mid:f.client.base.profile.mid,name:'new',display_name:'name',description:'',searchable:false};
 assert.equal((await executeSquare(f.client,'square_create',p,await squareContext(f.client,'square_create',p))).created,true);
});
test('moderation requires role and invite result validates exact unique recipients',async()=>{
 const f=fixture();f.community.myMembership.role='MEMBER';
 await assert.rejects(squareContext(f.client,'square_destroy_message',{target,message_id:'123'}),/role_required/);
 f.community.myMembership.role='ADMIN';let destroyed=0;f.client.base.square.destroyMessage=async()=>destroyed++;
 const d={target,message_id:'123'};await executeSquare(f.client,'square_destroy_message',d,await squareContext(f.client,'square_destroy_message',d));assert.equal(destroyed,1);
 const p={target,invitees:['u'+'8'.repeat(32),'u'+'9'.repeat(32)]};
 f.client.base.square.inviteIntoSquareChat=async()=>({inviteeMids:[p.invitees[0]]});
 assert.equal((await executeSquare(f.client,'square_invite',p,await squareContext(f.client,'square_invite',p))).complete,false);
 f.client.base.square.inviteIntoSquareChat=async()=>({inviteeMids:[p.invitees[0],p.invitees[0]]});
 await assert.rejects(executeSquare(f.client,'square_invite',p,await squareContext(f.client,'square_invite',p)),/unexpected_schema/);
});
test('media sends use SDK reqseq upload path without dummy message or reply',async()=>{
 const f=fixture();let sent=0;
 f.client.getSquareChat=async()=>({sendImage:async(blob,name)=>{sent++;assert.equal(blob.size,3);assert.equal(name,'attachment');return {objId:'456',objHash:'synthetic'};}});
 const p={target,media_type:'image',data:Buffer.from('abc').toString('base64')};
 assert.equal((await executeSquare(f.client,'square_send_media',p,await squareContext(f.client,'square_send_media',p))).id,'456');
 assert.equal(sent,1);assert.equal(f.calls.length,0);
});
test('media read verifies chat message and uses fixed OBS endpoint',async()=>{
 const {readSquareMedia}=await import('./square-actions.mjs');const f=fixture();f.message.contentType='IMAGE';
 f.message.contentMetadata={DOWNLOAD_URL:'https://attacker.invalid/token'};
 f.client.base.obs={downloadMessageData:async opts=>{assert.deepEqual(opts,{messageId:'123',isPreview:false,isSquare:true});return new Blob(['synthetic']);}};
 const result=await readSquareMedia(f.client,{target,message_id:'123',preview:false});assert.equal(result.data,Buffer.from('synthetic').toString('base64'));
 await assert.rejects(readSquareMedia(f.client,{target,message_id:'124',preview:false}),/not_found/);
});
