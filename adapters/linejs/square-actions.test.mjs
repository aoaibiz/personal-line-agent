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
