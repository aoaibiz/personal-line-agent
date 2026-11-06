/** Typed Square operations; no ordinary LINE MID is a Square identity. */
import {messageOptions,messagePage} from './openchat.mjs';
const targetId=x=>typeof x==='string'&&/^m[0-9a-f]{32}$/.test(x);
const memberId=x=>typeof x==='string'&&/^p[0-9a-f]{32}$/.test(x);
const squareId=x=>typeof x==='string'&&/^s[0-9a-f]{32}$/.test(x);
const msgId=x=>typeof x==='string'&&/^\d{1,30}$/.test(x);
export const squareKinds=['square_send_text','square_react','square_mark_read','square_unsend','square_rename_chat','square_leave_chat'];
export async function squareContext(client,kind,payload) {
 if(!squareKinds.includes(kind)||!targetId(payload.target)) throw Error('input_invalid');
 const detail=await client.base.square.getSquareChat({squareChatMid:payload.target});
 const chat=detail?.squareChat, member=detail?.squareChatMember;
 if(chat?.squareChatMid!==payload.target||!squareId(chat.squareMid)||member?.squareChatMid!==payload.target||!memberId(member.squareMemberMid)||!['JOINED',1].includes(member.membershipState)||!['ALIVE',0].includes(chat.state)) throw Error('square_not_member');
 const community=await client.base.square.getSquare({squareMid:chat.squareMid});
 const own=community?.myMembership;
 if(community?.square?.mid!==chat.squareMid||own?.squareMid!==chat.squareMid||own?.squareMemberMid!==member.squareMemberMid||!['JOINED',2].includes(own.membershipState)) throw Error('square_not_member');
 if(!/^\d{1,19}$/.test(String(member.revision))||BigInt(member.revision)>9223372036854775807n) throw Error('unexpected_schema');
 const context={target:payload.target,square:chat.squareMid,member:member.squareMemberMid,role:own.role,revision:String(member.revision),chat_revision:String(chat.squareChatRevision),name:chat.name};
 if(kind==='square_rename_chat'&&!['ADMIN','CO_ADMIN',1,2].includes(own.role)) throw Error('square_role_required');
 const id=payload.message_id??payload.reply_to;
 if(id!==undefined&&id!==null) {
  if(!msgId(id)) throw Error('input_invalid');
  const page=messagePage(await client.base.square.fetchSquareChatEvents(messageOptions(payload.target)),payload.target);
  const rows=page.messages.filter(m=>String(m.id)===id);
  if(rows.length!==1||!memberId(rows[0].from)) throw Error('square_message_not_found');
  const m=rows[0];
  if(kind==='square_unsend'&&m.from!==member.squareMemberMid) throw Error('square_own_message_required');
  context.message={id:String(m.id),from:m.from,to:m.to,text:m.text??null,contentType:m.contentType??null};
 }
 return context;
}
export async function executeSquare(client,kind,payload,expected) {
 const context=await squareContext(client,kind,payload);
 if(JSON.stringify(context)!==JSON.stringify(expected)) throw Error('square_context_changed');
 let result;
 if(kind==='square_send_text') {
  if(typeof payload.text!=='string'||payload.text.length<1||payload.text.length>10000) throw Error('input_invalid');
  result=await client.base.square.sendMessage({squareChatMid:payload.target,text:payload.text,...(payload.reply_to?{relatedMessageId:payload.reply_to}:{})});
  const message=result?.createdSquareMessage?.message;
  if(!message||message.to!==payload.target||!msgId(String(message.id))) throw Error('unexpected_schema');
  return {id:String(message.id)};
 }
 if(kind==='square_react') {
  if(!['UNDO','NICE','LOVE','FUN','AMAZING','SAD','OMG'].includes(payload.reaction)) throw Error('input_invalid');
  await client.base.square.reactToMessage({request:{reqSeq:await client.base.getReqseq('sq'),squareChatMid:payload.target,messageId:payload.message_id,reactionType:payload.reaction}});
 }
 if(kind==='square_mark_read') await client.base.square.markAsRead({request:{squareChatMid:payload.target,messageId:payload.message_id,threadMid:''}});
 if(kind==='square_unsend') await client.base.square.unsendMessage({squareChatMid:payload.target,messageId:payload.message_id});
 if(kind==='square_rename_chat') {
  if(typeof payload.name!=='string'||!payload.name.length||payload.name.length>50) throw Error('input_invalid');
  await (await client.getSquareChat(payload.target)).updateName(payload.name);
 }
 if(kind==='square_leave_chat') await client.base.square.leaveSquareChat({request:{squareChatMid:payload.target,sayGoodbye:false,squareChatMemberRevision:BigInt(context.revision)}});
 return {applied:true};
}
